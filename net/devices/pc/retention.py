"""Retention removes superseded API logs only after in-flight use finishes."""
from django.db import transaction
from django.db.models import Exists, OuterRef, CharField, Q
from django.db.models.functions import Cast
from net.models import Computer, ComputerLogFile, TaskTargetRun, TaskRun

def cleanup_logs_for_computer(computer_id):
    # Caller owns the Computer row lock, shared by ingestion and queue creation.
    active = TaskTargetRun.objects.filter(target_type='computer_log',
        target_id=Cast(OuterRef('pk'), CharField()), task__status__in=TaskRun.ACTIVE_STATUSES)
    stale = ComputerLogFile.objects.filter(computer_id=computer_id, retained=False)
    ids = list(stale.annotate(in_use=Exists(active)).filter(in_use=False).values_list('pk', flat=True)[:200])
    if ids:
        ComputerLogFile.objects.filter(pk__in=ids).delete()
    return len(ids)

def cleanup_retained_logs(limit=20):
    ids = list(ComputerLogFile.objects.filter(retained=False)
               .order_by('computer_id').values_list('computer_id', flat=True).distinct()[:limit])
    count = 0
    for identity in ids:
        with transaction.atomic():
            Computer.objects.select_for_update().get(pk=identity)
            count += cleanup_logs_for_computer(identity)
    return count + cleanup_analysis_results(limit=limit * 10)

def _notification_complete(query):
    return query.filter(
        Q(task_target__isnull=True)
        | Q(
            task_target__alert_processed_at__isnull=False,
            task_target__task__alert_summary_processed_at__isnull=False,
        )
    )


def remove_analysis_results(query):
    """Expire results only after target and task alert processing has finished."""
    rows = list(_notification_complete(query).values('pk', 'task_target_id')[:200])
    target_ids = [row['task_target_id'] for row in rows if row['task_target_id']]
    for target in TaskTargetRun.objects.filter(pk__in=target_ids):
        snapshot = dict(target.result_snapshot or {})
        snapshot.update(
            record_expired=True,
            retention_note='分析结果已按每天最新保留策略替换。',
        )
        TaskTargetRun.objects.filter(pk=target.pk).update(result_snapshot=snapshot)
    if rows:
        query.model.objects.filter(pk__in=[row['pk'] for row in rows]).delete()
    return len(rows)


def cleanup_analysis_results(limit=200):
    from net.models import ComputerAnalysis

    newer = ComputerAnalysis.objects.filter(
        computer_id=OuterRef('computer_id'),
        analysis_profile_id=OuterRef('analysis_profile_id'),
        analysis_date=OuterRef('analysis_date'),
        retention_managed=True,
        task_target__task__profile_snapshot__analysis_retention='daily_latest',
    ).filter(
        Q(created_at__gt=OuterRef('created_at'))
        | Q(created_at=OuterRef('created_at'), pk__gt=OuterRef('pk'))
    )
    candidates = ComputerAnalysis.objects.filter(retention_managed=True).annotate(
        superseded=Exists(newer),
    ).filter(superseded=True)
    computer_ids = list(
        _notification_complete(candidates)
        .order_by('computer_id')
        .values_list('computer_id', flat=True)
        .distinct()[:limit]
    )
    count = 0
    for computer_id in computer_ids:
        with transaction.atomic():
            Computer.objects.select_for_update().get(pk=computer_id)
            count += remove_analysis_results(candidates.filter(computer_id=computer_id))
    return count
