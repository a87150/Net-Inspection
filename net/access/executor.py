"""Lease-fenced access record publication; platform I/O occurs outside DB transactions."""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from net.access.adapters import build_adapter
from net.access.tasks import source_matches_task
from net.inspections.executor import ExecutionOutcome, _begin_target, _database_guard, _has_live_lease, _target_id
from net.inspections.state import save_target
from net.models.access import AccessRecord, AccessRecordSource
from net.models.tasks import TaskRun, TaskTargetRun

MAX_EVENTS_PER_SYNC = 10000


def _window(task):
    parameters = task.parameters_snapshot if isinstance(task.parameters_snapshot, dict) else {}
    try:
        start_at, end_at = (datetime.fromisoformat(parameters['window_start']), datetime.fromisoformat(parameters['window_end']))
    except (KeyError, TypeError, ValueError):
        raise ValidationError('门禁任务采集范围无效。') from None
    if timezone.is_naive(start_at) or timezone.is_naive(end_at) or start_at >= end_at:
        raise ValidationError('门禁任务采集范围无效。')
    return start_at, end_at


def _publish(started, worker_id, events, error='', lease_guard=None):
    with _database_guard(), transaction.atomic():
        target = TaskTargetRun.objects.select_for_update().select_related('task').get(pk=started.pk)
        task = TaskRun.objects.select_for_update().get(pk=target.task_id)
        now = timezone.now()
        if (not _has_live_lease(task, worker_id, now) or (lease_guard and lease_guard.is_set())
                or task.attempt_count != started.task.attempt_count or target.attempt_count != started.attempt_count
                or target.status != TaskRun.Status.RUNNING):
            return ExecutionOutcome(str(target.pk), target.status, stale=True)
        source = AccessRecordSource.objects.select_for_update().get(pk=target.target_id)
        if not source_matches_task(source, task):
            error = '门禁平台来源配置已变更或停用，请重新发起采集。'
        if not error:
            rows = []
            for event in events:
                if (not isinstance(event.event_id, str) or not event.event_id or len(event.event_id) > 255
                        or not isinstance(event.occurred_at, datetime) or timezone.is_naive(event.occurred_at)):
                    error = '平台返回的门禁记录缺少有效事件标识或时间。'; break
                rows.append(AccessRecord(source=source, source_event_id=event.event_id, occurred_at=event.occurred_at,
                    employee_number=event.employee_number[:255], person_name=event.person_name[:255], door_name=event.door_name[:255],
                    direction=event.direction, result=event.result, card_number=event.card_number[:255]))
            if not error:
                AccessRecord.objects.bulk_create(rows, ignore_conflicts=True, batch_size=500)
                _start, end_at = _window(task)
                source.cursor_at = max(value for value in (source.cursor_at, end_at) if value is not None)
                source.last_success_at, source.last_error = now, ''
                source.save(update_fields=('cursor_at', 'last_success_at', 'last_error', 'updated_at'))
        target.status = TaskRun.Status.FAILED if error else TaskRun.Status.SUCCESS
        target.finished_at, target.result_type, target.result_id = now, task.task_type, str(task.pk)
        target.result_snapshot = {'received': len(events)} if not error else {'error_category': 'access_platform'}
        target.error_message = error
        save_target(target, {'status', 'finished_at', 'result_type', 'result_id', 'result_snapshot', 'error_message'})
        if error: AccessRecordSource.objects.filter(pk=source.pk).update(last_error=error)
        return ExecutionOutcome(str(target.pk), target.status, result_type=target.result_type, result_id=target.result_id, error_message=error)


def execute_access_target(target_run, *, worker_id, lease_guard=None):
    started = _begin_target(_target_id(target_run), worker_id, lease_guard, expected_task_attempt=getattr(target_run.task, 'attempt_count', None))
    if started is None: return ExecutionOutcome(_target_id(target_run), TaskRun.Status.QUEUED, stale=True)
    if started.status in TaskRun.TERMINAL_STATUSES: return ExecutionOutcome(str(started.pk), started.status)
    events, error = [], ''
    try:
        source = AccessRecordSource.objects.get(pk=started.target_id)
        if not source_matches_task(source, started.task): raise ValidationError('门禁平台来源配置已变更或停用。')
        start_at, end_at = _window(started.task)
        for event in build_adapter(source).fetch_events(start_at=start_at, end_at=end_at, cancelled=lease_guard):
            events.append(event)
            if len(events) > MAX_EVENTS_PER_SYNC: raise ValidationError('门禁事件超过单次 10000 条上限，请缩小时间范围。')
    except ValidationError as exc: error = str(exc)
    except Exception as exc: error = f'门禁平台采集失败（{type(exc).__name__}）。'
    return _publish(started, worker_id, events, error, lease_guard)


def persist_access_failure(target_run, *, worker_id, error, lease_guard=None):
    started = TaskTargetRun.objects.select_related('task').get(pk=_target_id(target_run))
    if hasattr(target_run, 'task'): started.task = target_run.task
    return _publish(started, worker_id, [], '门禁记录采集执行或结果保存失败。', lease_guard)
