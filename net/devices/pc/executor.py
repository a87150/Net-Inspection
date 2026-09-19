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
