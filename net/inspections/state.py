"""Validated state transitions shared by queue services and future workers."""

from django.core.exceptions import ValidationError
from django.db.models import Count, Q

from net.models import TaskRun
from net.infrastructure.sanitization import sanitize


MAX_TASK_ATTEMPTS = 3
MAX_ERROR_SUMMARY_CHARS = 2048


def require_positive_int(value, field_name):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValidationError({field_name: '必须是正整数。'})


def save_task(task, update_fields):
    """Save a task through all model-level state and audit contracts."""
    task.active_scope_key = (
        task.scope_key if task.status in TaskRun.ACTIVE_STATUSES else None
    )
    task.full_clean()
    task.save(update_fields=update_fields)


def target_progress(targets):
    return targets.aggregate(total=Count('pk'),
        completed=Count('pk', filter=Q(status__in=TaskRun.TERMINAL_STATUSES)),
        successful=Count('pk', filter=Q(status=TaskRun.Status.SUCCESS)))


def save_target(target, update_fields):
    """Save a task target through immutable-history model contracts."""
    target.full_clean()
    target.save(update_fields=update_fields)
    if target_is_terminal(target):
        # Callers already hold the parent lease/transaction lock. Recompute from
        # rows, never increment, so retries cannot double-count progress.
        counts = target_progress(target.task.target_runs.all())
        completed, successes = counts['completed'], counts['successful']
        TaskRun.objects.filter(pk=target.task_id, status=TaskRun.Status.RUNNING).update(
            completed_targets=completed, successful_targets=successes,
            failed_targets=completed-successes, progress=int(100*completed/counts['total']) if counts['total'] else 0)


def target_is_terminal(target):
    return target.status in TaskRun.TERMINAL_STATUSES


def target_failed(target):
    return target.status != TaskRun.Status.SUCCESS


def bounded_error_summary(targets):
    messages = []
    for target in targets:
        if target.error_message:
            messages.append(
                f'{target.target_type}:{target.target_id}: {target.error_message}'
            )
    return sanitize('\n'.join(messages))[:MAX_ERROR_SUMMARY_CHARS]
