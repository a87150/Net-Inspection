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
from net.services.computer_analysis import analyze_log
from net.services.computer_logs import ScanSummary, scan_log_directory
from net.services.sanitization import sanitize
from net.tasks.queue import enqueue_task
from net.tasks.state import save_target

from .inspection import (
    ExecutionOutcome,
    _TERMINAL_STATUSES,
    _begin_target,
    _database_guard,
    _has_live_lease,
    _target_id,
)


def scan_profile_logs(profile, now=None):
    """Compatibility wrapper for Worker-owned scan execution only."""
    return scan_log_directory(profile, now=now)


def _snapshot_profile(task):
    """Rebuild the scan configuration from the immutable task snapshot."""
    snapshot = task.profile_snapshot if isinstance(task.profile_snapshot, dict) else {}
    if task.analysis_profile_id is None:
        raise ValidationError({'profile': '日志扫描任务缺少分析配置。'})
    profile = ComputerAnalysisProfile.objects.get(pk=task.analysis_profile_id)
    profile.name = str(snapshot.get('name') or profile.name)
    profile.is_enabled = True
    profile.scan_directories = list(snapshot.get('scan_directories') or [])
    profile.recursive = bool(snapshot.get('recursive'))
    profile.processed_directory = str(snapshot.get('processed_directory') or '')
    profile.failed_directory = str(snapshot.get('failed_directory') or '')
    profile.file_time_mode = snapshot.get(
        'file_time_mode', ComputerAnalysisProfile.FileTimeMode.RECENT_DAYS,
    )
    profile.recent_days = snapshot.get('recent_days')
    profile.range_start_date = _snapshot_date(snapshot.get('range_start_date'))
    profile.range_end_date = _snapshot_date(snapshot.get('range_end_date'))
    profile.analysis_items = list(snapshot.get('analysis_items') or [])
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
    if summary.failed or summary.move_failures:
        return TaskRun.Status.PARTIAL if log_ids else TaskRun.Status.FAILED
    return TaskRun.Status.SUCCESS


def _scan_message(summary):
    message = (
        f'扫描完成：导入 {summary.imported}，重复 {summary.duplicate}，'
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
            } | {str(pk) for pk in target.scan_logs.values_list('pk', flat=True)})
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
            target.result_type = 'computer_analysis_task' if child_task is not None else ''
            target.result_id = str(child_task.pk) if child_task is not None else ''
            target.result_snapshot = {
                'result_type': 'computer_scan',
                'analysis_task_id': str(child_task.pk) if child_task is not None else '',
                'imported': summary.imported,
                'duplicate': summary.duplicate,
                'failed': summary.failed,
                'skipped': summary.skipped,
                'move_failures': summary.move_failures,
                'log_ids': log_ids,
            }
            target.error_message = '' if target.status == TaskRun.Status.SUCCESS else message
            save_target(target, {
                'status', 'finished_at', 'result_type', 'result_id',
                'result_snapshot', 'error_message',
            })
            return ExecutionOutcome(
                str(target.pk), target.status, result_type=target.result_type,
                result_id=target.result_id, error_message=target.error_message,
            )


def execute_computer_scan_target(target_run, *, worker_id, lease_guard=None):
    """Scan server folders under a Worker lease, then queue imported logs."""
    target_run_id = _target_id(target_run)
    started = _begin_target(target_run_id, worker_id, lease_guard)
    if started is None:
        return ExecutionOutcome(target_run_id, TaskRun.Status.QUEUED, stale=True)
    if started.status in _TERMINAL_STATUSES:
        return ExecutionOutcome(target_run_id, started.status)
    if started.target_type != TaskTargetRun.TargetType.COMPUTER_SCAN:
        return _failure(target_run_id, worker_id, '日志扫描任务包含无效目标类型。', lease_guard)
    if lease_guard is not None and lease_guard.is_set():
        return ExecutionOutcome(target_run_id, started.status, stale=True)
    try:
        summary = scan_log_directory(_snapshot_profile(started.task), scan_target=started)
    except (ValidationError, OSError, ValueError, TypeError) as exc:
        if not started.scan_logs.exists():
            return _failure(target_run_id, worker_id, f'日志扫描失败：{exc}', lease_guard)
        # An unavailable input directory cannot erase committed import intent.
        # Handoff uses persisted evidence only, and reports the scan as partial.
        summary = ScanSummary(failed=1, errors=[f'日志扫描失败：{exc}'])
    return _persist_scan(target_run_id, worker_id, summary, lease_guard)


def persist_computer_scan_failure(target_run, *, worker_id, error, lease_guard=None):
    """Record unexpected scan executor errors behind the same lease fence."""
    target_run_id = _target_id(target_run)
    if TaskTargetRun.objects.filter(pk=target_run_id, scan_logs__isnull=False).exists():
        # Imported evidence already has an immutable handoff obligation. A
        # transient enqueue/link failure must remain eligible for lease replay.
        return ExecutionOutcome(target_run_id, TaskRun.Status.RUNNING, stale=True)
    started = _begin_target(target_run_id, worker_id, lease_guard)
    if started is not None and started.status in _TERMINAL_STATUSES:
        return ExecutionOutcome(target_run_id, started.status)
    return _failure(
        target_run_id,
        worker_id,
        f'日志扫描执行失败：{sanitize(str(error))}',
        lease_guard,
    )


def _failure(target_run_id, worker_id, message, lease_guard=None):
    """Terminally fail one analysis target only while the lease is still live."""
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
            message = sanitize(message)[:4096]
            target.status = TaskRun.Status.FAILED
            target.finished_at = now
            target.error_message = message
            save_target(target, {'status', 'finished_at', 'error_message'})
            return ExecutionOutcome(
                str(target.pk),
                TaskRun.Status.FAILED,
                error_message=message,
            )


def _persist_analysis(target_run_id, worker_id, lease_guard=None):
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
            log_file = ComputerLogFile.objects.filter(pk=target.target_id).first()
            if log_file is None:
                return _failure(
                    target.pk,
                    worker_id,
                    '日志文件已不存在，无法执行分析。',
                    lease_guard,
                )
            try:
                analysis = analyze_log(
                    log_file,
                    list(task.selected_items_snapshot),
                    task_target=target,
                    started_at=target.started_at or now,
                )
            except (ValidationError, ValueError, TypeError) as exc:
                return _failure(target.pk, worker_id, f'日志分析失败：{exc}', lease_guard)

            target.status = analysis.status
            target.finished_at = now
            target.result_type = 'computer_analysis'
            target.result_id = str(analysis.pk)
            target.result_snapshot = {
                'result_type': 'computer_analysis',
                'result_id': str(analysis.pk),
                'status': analysis.status,
                'summary': analysis.summary,
                'details': analysis.details,
                'analysis_items': analysis.analysis_items,
                'exceptions': analysis.exceptions,
            }
            target.error_message = '' if analysis.status == TaskRun.Status.SUCCESS else analysis.summary
            save_target(
                target,
                {
                    'status', 'finished_at', 'result_type', 'result_id',
                    'result_snapshot', 'error_message',
                },
            )
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
    outcome = _persist_analysis(target_run_id, worker_id, lease_guard)
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
