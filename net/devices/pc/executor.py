"""Lease-fenced execution of analysis targets backed by imported JSON logs."""

from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils import timezone

from net.models import (
    ComputerAnalysisProfile,
    ComputerLogFile,
    TaskRun,
    TaskTargetRun,
)
from net.devices.pc.analysis import prepare_log, persist_analysis
from net.devices.pc.logs import LogLeaseLost
from net.devices.pc.remote_ingestion import FetchSummary, fetch_remote_logs
from net.devices.pc.configuration import source_from_snapshot
from net.devices.pc.connectors.base import PCLogConnectionError
from net.infrastructure.sanitization import sanitize
from net.inspections.queue import enqueue_task
from net.inspections.state import save_target
from net.inspections.result_storage import compact_result_snapshot
from net.inspections.locking import locked_task_target

from net.inspections.executor import (
    ExecutionOutcome,
    _TERMINAL_STATUSES,
    _begin_target,
    _database_guard,
    _has_live_lease,
    _target_id,
)
def _snapshot_profile(task):
    """Rebuild the scan configuration from the immutable task snapshot."""
    snapshot = task.profile_snapshot if isinstance(task.profile_snapshot, dict) else {}
    if task.analysis_profile_id is None:
        raise ValidationError({'profile': '日志获取任务缺少分析配置。'})
    profile = ComputerAnalysisProfile.objects.get(pk=task.analysis_profile_id)
    profile.name = str(snapshot.get('name') or profile.name)
    profile.is_enabled = True
    profile.analysis_items = list(snapshot.get('analysis_items') or [])
    profile.matching_mode = snapshot.get('matching_mode', 'logs')
    profile.software_policy_path = str(snapshot.get('software_policy_path') or '')
    profile.software_policy_mode = snapshot.get('software_policy_mode', 'whitelist')
    profile.minimum_windows_release = str(snapshot.get('minimum_windows_release') or '')
    profile.defender_update_max_days = snapshot.get('defender_update_max_days', 7)
    profile.defender_scan_max_days = snapshot.get('defender_scan_max_days', 7)
    profile.patch_max_days = snapshot.get('patch_max_days', 30)
    profile.uptime_max_hours = snapshot.get('uptime_max_hours', 168)
    profile.disk_max_percent = snapshot.get('disk_max_percent', 90)
    profile.cpu_max_percent = snapshot.get('cpu_max_percent', 90)
    profile.cpu_temperature_max_celsius = snapshot.get('cpu_temperature_max_celsius', 85)
    profile.site_ip_prefixes = snapshot.get('site_ip_prefixes', {})
    profile.memory_max_percent = snapshot.get('memory_max_percent', 90)
    profile.kms_servers = list(snapshot.get('kms_servers') or [])
    profile.concurrent_workers = snapshot.get('concurrent_workers', 1)
    profile.alert_policy_mode = snapshot.get('alert_policy_mode', 'inherit')
    profile.full_clean()
    return profile


def _snapshot_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, date):
        return value
    parsed = parse_date(str(value))
    if parsed is None:
        raise ValidationError({'profile_snapshot': '日志日期范围快照无效。'})
    return parsed


def _scan_status(summary, log_ids):
    if summary.failed or summary.move_failures or summary.errors:
        return TaskRun.Status.PARTIAL if log_ids else TaskRun.Status.FAILED
    return TaskRun.Status.SUCCESS


def _scan_message(summary):
    message = (
        f'获取完成：导入 {summary.imported}，重复 {summary.duplicate}，'
        f'失败 {summary.failed}，跳过 {summary.skipped}，移动失败 {summary.move_failures}。'
    )
    if summary.errors:
        message = f'{message} ' + '；'.join(str(error) for error in summary.errors)
    return sanitize(message)[:4096]


def _enqueue_scanned_analyses(task, log_ids):
    if not log_ids:
        return None
    profile = _snapshot_profile(task)
    overrides = {'selected_items': list(task.selected_items_snapshot),
                 'parameters': {**task.parameters_snapshot, 'concurrent_workers': task.parameters_snapshot.get(
                     'concurrent_workers', task.profile_snapshot.get('concurrent_workers', 1))}}
    if task.source == TaskRun.Source.SCHEDULED:
        overrides['schedule'] = task.schedule
    return enqueue_task(profile, log_ids, task.source, overrides=overrides, _frozen_parent=task)


def _persist_scan(target_run_id, worker_id, summary, lease_guard=None):
    try:
        return _persist_scan_atomic(target_run_id, worker_id, summary, lease_guard)
    except ValidationError:
        # An overlapping active analysis may prevent enqueue. Lease recovery
        # retries only the durable intended logs under the frozen parent context.
        return ExecutionOutcome(str(target_run_id), TaskRun.Status.RUNNING, stale=True)


def reconcile_pending_analysis_handoffs(*, limit=20):
    """Drain committed analysis intent after exhausted fetches, without network IO."""
    ids = list(TaskTargetRun.objects.filter(
        task__task_type=TaskRun.TaskType.COMPUTER_FETCH,
        task__status=TaskRun.Status.FAILED,
        analysis_handoff_task__isnull=True,
        result_snapshot__analysis_handoff_pending=True,
    ).order_by('created_at').values_list('pk', flat=True)[:limit])
    restored = 0
    for identity in ids:
        try:
            with transaction.atomic():
                # Same parent-then-target lock order as queue recovery.
                task_id = TaskTargetRun.objects.values_list('task_id', flat=True).get(pk=identity)
                task = TaskRun.objects.select_for_update().get(pk=task_id)
                target = TaskTargetRun.objects.select_for_update().get(pk=identity)
                if (task.status != TaskRun.Status.FAILED or target.analysis_handoff_task_id
                        or not target.result_snapshot.get('analysis_handoff_pending')):
                    continue
                log_ids = list(target.fetched_logs.filter(import_status='imported').values_list('pk', flat=True))
                child = _enqueue_scanned_analyses(task, log_ids)
                if child is None:
                    continue
                target.analysis_handoff_task = child
                target.error_message += '\n已入库日志的分析交接已恢复；获取任务仍保留失败状态。'
                save_target(target, {'analysis_handoff_task', 'error_message'})
                restored += 1
        except ValidationError:
            # A currently overlapping analysis may block insertion; retain the
            # visible pending flag for the next Worker poll, not another fetch.
            continue
    return restored


def _persist_scan_atomic(target_run_id, worker_id, summary, lease_guard):
    with _database_guard():
        with transaction.atomic():
            target = (
                TaskTargetRun.objects.select_for_update()
                .select_related('task')
                .filter(pk=target_run_id)
                .first()
            )
            if target is None:
                return ExecutionOutcome(str(target_run_id), TaskRun.Status.FAILED, stale=True)
            task = TaskRun.objects.select_for_update().filter(pk=target.task_id).first()
            now = timezone.now()
            if (
                (lease_guard is not None and lease_guard.is_set())
                or task is None
                or not _has_live_lease(task, worker_id, now)
                or target.status != TaskRun.Status.RUNNING
            ):
                return ExecutionOutcome(str(target.pk), target.status, stale=True)
            log_ids = sorted({
                str(log_file.pk)
                for log_file in summary.log_files
                if log_file.import_status == 'imported'
            } | {str(pk) for pk in target.fetched_logs.values_list('pk', flat=True)})
            # Queue insertion and parent link share a commit boundary. A child
            # cannot become visible to a Worker before this lease-fenced link.
            child_task = _enqueue_scanned_analyses(task, log_ids)
            task = TaskRun.objects.select_for_update().filter(pk=target.task_id).first()
            now = timezone.now()
            if (
                (lease_guard is not None and lease_guard.is_set())
                or task is None
                or not _has_live_lease(task, worker_id, now)
                or target.status != TaskRun.Status.RUNNING
            ):
                transaction.set_rollback(True)
                return ExecutionOutcome(str(target.pk), target.status, stale=True)
            message = _scan_message(summary)
            target.status = _scan_status(summary, log_ids)
            target.finished_at = now
            target.analysis_handoff_task = child_task
            target.result_type = 'computer_analysis_task' if child_task is not None else ''
            target.result_id = str(child_task.pk) if child_task is not None else ''
            target.result_snapshot = {
                'result_type': 'computer_fetch',
                'analysis_task_id': str(child_task.pk) if child_task is not None else '',
                'imported': summary.imported,
                'reused': task.parameters_snapshot.get('reused_log_count', 0),
                'analysis_log_count': len(log_ids),
                'discovered': summary.discovered,
                'downloaded': summary.downloaded,
                'duplicate': summary.duplicate,
                'failed': summary.failed,
                'skipped': summary.skipped,
                'move_failures': summary.move_failures,
                'log_ids': log_ids,
            }
            target.error_message = '' if target.status == TaskRun.Status.SUCCESS else message
            save_target(target, {
                'status', 'finished_at', 'result_type', 'result_id',
                'result_snapshot', 'error_message', 'analysis_handoff_task',
            })
            return ExecutionOutcome(
                str(target.pk), target.status, result_type=target.result_type,
                result_id=target.result_id, error_message=target.error_message,
            )


def execute_computer_fetch_target(target_run, *, worker_id, lease_guard=None, max_download_workers=1):
    """Fetch the remote inbox under a Worker lease, then queue imported logs."""
    target_run_id = _target_id(target_run)
    started = _begin_target(target_run_id, worker_id, lease_guard)
    if started is None:
        return ExecutionOutcome(target_run_id, TaskRun.Status.QUEUED, stale=True)
    if started.status in _TERMINAL_STATUSES:
        return ExecutionOutcome(target_run_id, started.status)
    if started.target_type != TaskTargetRun.TargetType.COMPUTER_SOURCE:
        return _failure(target_run_id, worker_id, '日志获取任务包含无效目标类型。', lease_guard)
    if lease_guard is not None and lease_guard.is_set():
        return ExecutionOutcome(target_run_id, started.status, stale=True)
    try:
        source = source_from_snapshot(started.task.profile_snapshot.get('log_source'))
        options = {}
        if started.task.parameters_snapshot.get('analysis_scope') == 'source_window':
            from django.utils.dateparse import parse_datetime
            options['now'] = parse_datetime(started.task.parameters_snapshot['log_window_at'])
        summary = fetch_remote_logs(source, task_target=started,
                                    max_download_workers=max_download_workers, **options)
    except LogLeaseLost:
        return ExecutionOutcome(target_run_id, started.status, stale=True)
    except (ValidationError, PCLogConnectionError, OSError, ValueError, TypeError) as exc:
        if not started.fetched_logs.exists():
            return _failure(target_run_id, worker_id, f'日志获取失败：{exc}', lease_guard)
        # An unavailable input directory cannot erase committed import intent.
        # Handoff uses persisted evidence only, and reports the scan as partial.
        summary = FetchSummary(failed=1, errors=[f'日志获取失败：{exc}'])
    return _persist_scan(target_run_id, worker_id, summary, lease_guard)


def persist_computer_fetch_failure(target_run, *, worker_id, error, lease_guard=None):
    """Record unexpected scan executor errors behind the same lease fence."""
    target_run_id = _target_id(target_run)
    if TaskTargetRun.objects.filter(pk=target_run_id, fetched_logs__isnull=False).exists():
        # Imported evidence already has an immutable handoff obligation. A
        # transient enqueue/link failure must remain eligible for lease replay.
        return ExecutionOutcome(target_run_id, TaskRun.Status.RUNNING, stale=True)
    started = _begin_target(target_run_id, worker_id, lease_guard)
    if started is not None and started.status in _TERMINAL_STATUSES:
        return ExecutionOutcome(target_run_id, started.status)
    return _failure(
        target_run_id,
        worker_id,
        f'日志获取执行失败：{sanitize(str(error))}',
        lease_guard,
    )


def _analysis_lease_live(task, worker_id, lease_guard):
    # The parent remains locked throughout persistence; only wall time and the
    # external guard can change independently of this transaction.
    return (not (lease_guard is not None and lease_guard.is_set())
            and _has_live_lease(task, worker_id, timezone.now()))


def _rollback_analysis(target):
    transaction.set_rollback(True)
    return ExecutionOutcome(str(target.pk), TaskRun.Status.RUNNING, stale=True)


def _failure(target_run_id, worker_id, message, lease_guard=None, *, expected_attempts=None):
    """Terminally fail one analysis target only while the lease is still live."""
    with _database_guard():
        with transaction.atomic():
            task, target = locked_task_target(target_run_id)
            if target is None:
                return ExecutionOutcome(str(target_run_id), TaskRun.Status.FAILED, stale=True)
            now = timezone.now()
            if (
                (lease_guard is not None and lease_guard.is_set())
                or task is None
                or not _has_live_lease(task, worker_id, now)
                or target.status != TaskRun.Status.RUNNING
            ):
                return ExecutionOutcome(str(target.pk), target.status, stale=True)
            message = sanitize(message)[:4096]
            if expected_attempts is not None and expected_attempts != (task.attempt_count, target.attempt_count):
                return ExecutionOutcome(str(target.pk), target.status, stale=True)
            target.status = TaskRun.Status.FAILED
            target.finished_at = now
            target.error_message = message
            save_target(target, {'status', 'finished_at', 'error_message'})
            if not _analysis_lease_live(task, worker_id, lease_guard):
                return _rollback_analysis(target)
            return ExecutionOutcome(
                str(target.pk),
                TaskRun.Status.FAILED,
                error_message=message,
            )


def _persist_analysis(target_run_id, worker_id, prepared, expected_attempts, lease_guard=None):
    with _database_guard():
        with transaction.atomic():
            task, target = locked_task_target(target_run_id)
            if target is None:
                return ExecutionOutcome(str(target_run_id), TaskRun.Status.FAILED, stale=True)
            now = timezone.now()
            if (
                (lease_guard is not None and lease_guard.is_set())
                or task is None
                or not _has_live_lease(task, worker_id, now)
                or target.status != TaskRun.Status.RUNNING
            ):
                return ExecutionOutcome(str(target.pk), target.status, stale=True)
            if expected_attempts != (task.attempt_count, target.attempt_count):
                return ExecutionOutcome(str(target.pk), target.status, stale=True)
            if prepared is None:
                target.status = TaskRun.Status.SUCCESS
                target.finished_at = timezone.now()
                target.result_type = target.result_id = ''
                target.result_snapshot = {
                    'ignored': True,
                    'summary': '以人员为主：日志未匹配人员，已忽略，不参与分析与告警。',
                }
                target.error_message = ''
                save_target(target, {'status', 'finished_at', 'result_type', 'result_id',
                                     'result_snapshot', 'error_message'})
                if not _analysis_lease_live(task, worker_id, lease_guard):
                    return _rollback_analysis(target)
                return ExecutionOutcome(str(target.pk), target.status)
            try:
                # Include caller-side failures after insertion in the savepoint.
                with transaction.atomic():
                    analysis = persist_analysis(prepared)
            except (ValidationError, ValueError, TypeError) as exc:
                return _failure(target.pk, worker_id, f'日志分析失败：{exc}', lease_guard,
                                expected_attempts=expected_attempts)

            if not _analysis_lease_live(task, worker_id, lease_guard):
                return _rollback_analysis(target)
            target.status = analysis.status
            target.finished_at = timezone.now()
            target.result_type = 'computer_analysis'
            target.result_id = str(analysis.pk)
            target.result_snapshot = compact_result_snapshot(analysis, result_type='computer_analysis')
            target.error_message = '' if analysis.status == TaskRun.Status.SUCCESS else analysis.summary
            save_target(
                target,
                {
                    'status', 'finished_at', 'result_type', 'result_id',
                    'result_snapshot', 'error_message',
                },
            )
            if not _analysis_lease_live(task, worker_id, lease_guard):
                return _rollback_analysis(target)
            return ExecutionOutcome(
                str(target.pk),
                analysis.status,
                result_type='computer_analysis',
                result_id=str(analysis.pk),
                error_message=target.error_message,
            )


def execute_computer_target(target_run, *, worker_id, lease_guard=None):
    """Analyze one already imported local log under the current Worker lease."""
    target_run_id = _target_id(target_run)
    started = _begin_target(target_run_id, worker_id, lease_guard)
    if started is None:
        return ExecutionOutcome(target_run_id, TaskRun.Status.QUEUED, stale=True)
    if started.status in _TERMINAL_STATUSES:
        return ExecutionOutcome(target_run_id, started.status)
    if started.target_type != TaskTargetRun.TargetType.COMPUTER_LOG:
        return _failure(target_run_id, worker_id, '计算机分析任务包含无效目标类型。', lease_guard)
    if lease_guard is not None and lease_guard.is_set():
        return ExecutionOutcome(target_run_id, started.status, stale=True)
    expected_attempts = (started.task.attempt_count, started.attempt_count)
    log_file = ComputerLogFile.objects.filter(pk=started.target_id).first()
    if log_file is None:
        outcome = _failure(target_run_id, worker_id, '日志文件已不存在，无法执行分析。',
                           lease_guard, expected_attempts=expected_attempts)
    else:
        if started.task.profile_snapshot.get('matching_mode') == 'people':
            from net.devices.pc.matching import match_person
            payload = log_file.payload if isinstance(log_file.payload, dict) else {}
            system = payload.get('系统信息概览', {})
            person, _ = match_person(system if isinstance(system, dict) else {},
                                     started.task.parameters_snapshot.get('personnel_roster', []))
            if person is None:
                return _persist_analysis(target_run_id, worker_id, None, expected_attempts, lease_guard)
        try:
            prepared = prepare_log(
                log_file, list(started.task.selected_items_snapshot),
                rules={**started.task.profile_snapshot,
                       'personnel_roster': started.task.parameters_snapshot.get('personnel_roster')},
                task_target=started, started_at=started.started_at,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            outcome = _failure(target_run_id, worker_id, f'日志分析失败：{exc}',
                               lease_guard, expected_attempts=expected_attempts)
        else:
            outcome = _persist_analysis(target_run_id, worker_id, prepared, expected_attempts, lease_guard)
    if not outcome.stale:
        from net.alerts.service import process_persisted_target

        with _database_guard():
            process_persisted_target(target_run_id)
    return outcome


def persist_computer_execution_failure(target_run, *, worker_id, error, lease_guard=None):
    """Record an unexpected executor error without borrowing inspection logic."""
    target_run_id = _target_id(target_run)
    started = _begin_target(target_run_id, worker_id, lease_guard)
    if started is not None and started.status in _TERMINAL_STATUSES:
        return ExecutionOutcome(target_run_id, started.status)
    return _failure(
        target_run_id,
        worker_id,
        f'日志分析执行失败：{sanitize(str(error))}',
        lease_guard,
    )
