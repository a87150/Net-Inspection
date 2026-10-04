"""Retention for infrastructure inspection records.

The three infrastructure inspection tables are the only ones that grow with
(devices x runs); the PC side has its own retention. Two rules keep them bounded
without breaking the product:

* Nothing is deleted until its task target AND task alert processing have finished,
  so an alert still being delivered never loses the record it is about.
* The newest record per asset is never deleted, because net.dashboard.assets derives
  every asset list row status (normal / abnormal / unchecked) from that one row.
  "不保留" therefore means "keep only the newest", not "delete everything".

Retention is read from the profile_snapshot of the task that produced the record, so
editing a profile does not rewrite history that other tasks generated. Tasks created
before this setting existed carry no key in their snapshot, so those fall back to
their profile's current value -- otherwise such rows would be unreachable by retention
and accumulate forever.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.db.models import PROTECT, RESTRICT
from django.utils import timezone

from net.infrastructure.retention import alert_processing_complete
from net.models import (RECORD_RETENTION_DAYS, Monitor_Inspection,
                        Network_Device_Inspection, Server_Inspection, TaskRun,
                        TaskTargetRun)

INSPECTION_RETENTION_TARGETS = (
    (Network_Device_Inspection, 'device'),
    (Server_Inspection, 'server'),
    (Monitor_Inspection, 'monitor'),
)


def _newest_per_asset(model, foreign_key):
    """Subquery selecting the newest record pk for the same asset as the outer row."""
    return model.objects.filter(
        **{f'{foreign_key}_id': OuterRef(f'{foreign_key}_id')},
    ).order_by('-created_at', '-pk').values('pk')[:1]


def cleanup_inspection_records(limit=200):
    """Delete inspection history past each profile record_retention window.

    Returns the number of inspection records expired. Cascaded error rows are not
    counted; they follow their inspection automatically.
    """
    now = timezone.now()
    expired = 0
    for model, foreign_key in INSPECTION_RETENTION_TARGETS:
        newest = _newest_per_asset(model, foreign_key)
        for setting, days in RECORD_RETENTION_DAYS.items():
            cutoff = now - timedelta(days=days)
            candidates = model.objects.filter(created_at__lt=cutoff).filter(
                # The task that produced the record carries the setting it ran under.
                Q(task_target__task__profile_snapshot__record_retention=setting)
                # Legacy tasks have no such key; fall back to the profile's current
                # value so their rows stay reachable instead of growing forever.
                | Q(
                    task_target__task__profile_snapshot__record_retention__isnull=True,
                    task_target__task__inspection_profile__record_retention=setting,
                )
            ).exclude(pk=Subquery(newest))
            ids = list(
                alert_processing_complete(candidates)
                .order_by('created_at').values_list('pk', flat=True)[:limit]
            )
            if not ids:
                continue
            with transaction.atomic():
                # Re-applied inside the DELETE so an alert whose processing started
                # between the SELECT and the DELETE cannot lose its record.
                alert_processing_complete(model.objects.filter(pk__in=ids)).delete()
            expired += len(ids)
    return expired

# Reverse PROTECT links that pin a finished task or one of its targets. A task's own
# TaskTargetRun children are handled first, so they are not listed as task blockers.
TASK_HISTORY_TASK_BLOCKERS = frozenset({
    'AlertEvent', 'DomainOperation', 'TopologyDiscoveryBatch',
})
TASK_HISTORY_TARGET_BLOCKERS = frozenset({
    'AlertEvent', 'ComputerAnalysis', 'Network_Device_Inspection',
    'Server_Inspection', 'Monitor_Inspection', 'TopologyDiscoveryBatch',
    'NetworkTopologyObservation',
})


def _strongly_referenced(instance, blocker_names):
    """True when a PROTECT/RESTRICT link in blocker_names still points at instance."""
    for relation in instance._meta.get_fields(include_hidden=True):
        if not relation.auto_created or relation.concrete:
            continue
        if not (relation.one_to_many or relation.one_to_one):
            continue
        field = relation.field
        if field.remote_field.on_delete not in (PROTECT, RESTRICT):
            continue
        if relation.related_model.__name__ not in blocker_names:
            continue
        if relation.related_model._base_manager.filter(
                **{field.attname: instance.pk}).exists():
            return True
    return False


def cleanup_task_history(retention_days, limit=200):
    """Delete finished tasks older than the window that nothing still points at.

    A task is only removed when neither it nor any of its target runs is held by
    a PROTECT link (alerts, domain operations, topology batches, or a record that
    retention has not expired yet). Blocked tasks are left for a later pass, so
    this cannot fail the way a plain bulk delete would.
    """
    cutoff = timezone.now() - timedelta(days=retention_days)
    candidates = TaskRun.objects.filter(
        status__in=TaskRun.TERMINAL_STATUSES,
        finished_at__lt=cutoff,
    ).order_by('finished_at', 'pk')[:limit]
    removed = 0
    for task in candidates:
        if _strongly_referenced(task, TASK_HISTORY_TASK_BLOCKERS):
            continue
        with transaction.atomic():
            targets = list(TaskTargetRun.objects.filter(task=task))
            if any(_strongly_referenced(target, TASK_HISTORY_TARGET_BLOCKERS)
                   for target in targets):
                continue
            TaskTargetRun.objects.filter(task=task).delete()
            task.delete()
        removed += 1
    return removed