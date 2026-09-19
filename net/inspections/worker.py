"""Cross-platform, lease-aware Worker for infrastructure task runs."""

from __future__ import annotations

import os
import logging
import traceback
import socket
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait

from django.core.exceptions import ValidationError
from django.db import OperationalError, close_old_connections, connections

from net.models import TaskRun

from net.devices.pc.executor import execute_computer_target, persist_computer_execution_failure
from net.domain.executor import (
    aggregate_domain_operation,
    abort_password_domain_task,
    execute_domain_target,
    mark_domain_operation_running,
    persist_domain_execution_failure,
    prepare_domain_task_context,
)
from net.inspections.executor import _database_guard, execute_target, persist_execution_failure
from net.people.executor import execute_people_target, persist_people_failure
from net.access.executor import execute_access_target, persist_access_failure
from net.domain.sync_tasks import execute_domain_sync_target, persist_domain_sync_failure
from .queue import (
    claim_next_task,
    finish_task,
    recover_expired_tasks,
    renew_lease,
    is_sqlite_busy,
)
from .schedules import enqueue_due_schedules
from net.devices.pc.retention import cleanup_retained_logs


def default_worker_id():
    return f'{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:12]}'


class _Cancellation:
    """Combine service stop with local lease loss without clearing either signal."""

    def __init__(self, stop_event):
        self.stop_event = stop_event
        self.lost_lease = threading.Event()

    def is_set(self):
        return self.lost_lease.is_set() or self.stop_event.is_set()

    def set(self):
        self.lost_lease.set()


class TaskWorker:
    """Claim one task at a time and isolate its targets in bounded threads."""

    def __init__(self, *, worker_id=None, threads=4, lease_seconds=60, poll_seconds=5):
        if not isinstance(threads, int) or isinstance(threads, bool) or threads <= 0:
            raise ValueError('threads 必须是正整数。')
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds <= 0:
            raise ValueError('lease_seconds 必须是正整数。')
        if not isinstance(poll_seconds, (int, float)) or poll_seconds <= 0:
            raise ValueError('poll_seconds 必须大于零。')
        self.worker_id = worker_id or default_worker_id()
        self.threads = threads
        self.lease_seconds = lease_seconds
        self.poll_seconds = float(poll_seconds)

    def _max_workers(self, task):
        parameters = task.parameters_snapshot if isinstance(task.parameters_snapshot, dict) else {}
        profile_limit = parameters.get(
            'concurrent_workers',
            (task.profile_snapshot or {}).get('concurrent_workers', 1),
        )
        try:
            profile_limit = int(profile_limit)
        except (TypeError, ValueError):
            profile_limit = 1
        return max(1, min(self.threads, max(1, profile_limit)))

    def _heartbeat_interval(self):
        return max(0.05, min(float(self.lease_seconds) / 3, 10.0))

    def _run_target(self, target, task_type, lease_guard, domain_context=None):
        """Each pool invocation owns and closes only its thread-local connections."""
        try:
            close_old_connections()
            if lease_guard.is_set():
                return
            try:
                if task_type == TaskRun.TaskType.DOMAIN_OPERATION:
                    return execute_domain_target(
                        target, worker_id=self.worker_id, lease_guard=lease_guard,
                        domain_context=domain_context,
                    )
                if task_type == TaskRun.TaskType.DOMAIN_SYNC:
                    executor = execute_domain_sync_target
                elif task_type == TaskRun.TaskType.ACCESS_SYNC:
                    executor = execute_access_target
                elif task_type in TaskRun.PEOPLE_TASK_TYPES:
                    executor = execute_people_target
                elif task_type == TaskRun.TaskType.COMPUTER_ANALYSIS:
                    executor = execute_computer_target
                else:
                    executor = execute_target
                return executor(target, worker_id=self.worker_id, lease_guard=lease_guard)
            except Exception as exc:
                database_message = ''
                if isinstance(exc, OperationalError):
                    if is_sqlite_busy(exc):
                        database_message = 'SQLite 数据库被占用，请稍后重试。'
                    elif any(text in str(exc).lower() for text in ('no such table', 'no such column')):
                        database_message = '数据库结构与代码不一致，请对当前运行数据库执行迁移。'
                    else:
                        database_message = '数据库操作失败，请查看 Worker 控制台定位信息。'
                    # Preserve call sites, but never log SQL parameters or raw DB errors
                    # which may contain imported personal data or credentials.
                    logging.getLogger(__name__).error(
                        'task=%s target=%s: %s\n%s', target.task_id, target.pk,
                        database_message, ''.join(traceback.format_tb(exc.__traceback__)),
                    )
                if not lease_guard.is_set():
                    try:
                        if task_type == TaskRun.TaskType.DOMAIN_OPERATION:
                            return persist_domain_execution_failure(
                                target, worker_id=self.worker_id,
                                error='域控目标执行失败。', lease_guard=lease_guard,
                                domain_context=domain_context,
                            )
                        if task_type == TaskRun.TaskType.DOMAIN_SYNC:
                            persist_failure = persist_domain_sync_failure
                        elif task_type == TaskRun.TaskType.ACCESS_SYNC:
                            persist_failure = persist_access_failure
                        elif task_type in TaskRun.PEOPLE_TASK_TYPES:
                            persist_failure = persist_people_failure
                        elif task_type == TaskRun.TaskType.COMPUTER_ANALYSIS:
                            persist_failure = persist_computer_execution_failure
                        else:
                            persist_failure = persist_execution_failure
                        return persist_failure(
                            target, worker_id=self.worker_id,
                            error=database_message or f'目标执行或结果保存失败（{type(exc).__name__}）',
                            lease_guard=lease_guard,
                        )
                    except Exception:
                        # Leave unfinished state recoverable by the existing lease contract.
                        return None
        finally:
            try:
                close_old_connections()
            finally:
                connections.close_all()

    def _execute_claimed_task(self, task, stop_event):
        target_runs = list(
            task.target_runs.filter(status=TaskRun.Status.QUEUED).order_by('created_at', 'pk')
        )
        for target in target_runs:
            target.task = task  # Retain this claim generation for executor failure fencing.
        lease_guard = _Cancellation(stop_event)
        domain_context = None
        if task.task_type == TaskRun.TaskType.DOMAIN_OPERATION and not lease_guard.is_set():
            try:
                domain_context = prepare_domain_task_context(
                    task,
                    worker_id=self.worker_id,
                    claim_generation=task.attempt_count,
                )
            except ValidationError:
                # All targets become terminal through the normal, lease-fenced
                # failure path; password text/ciphertext is never retained.
                domain_context = None
            if lease_guard.is_set():
                if domain_context is not None and domain_context.password:
                    abort_password_domain_task(
                        task,
                        worker_id=self.worker_id,
                        claim_generation=domain_context.task_attempt,
                    )
                return
            if not mark_domain_operation_running(
                task,
                worker_id=self.worker_id,
                claim_generation=task.attempt_count,
            ):
                return
        if not target_runs and not stop_event.is_set():
            try:
                finished = finish_task(task.pk, self.worker_id)
                if task.task_type == TaskRun.TaskType.DOMAIN_OPERATION:
                    aggregate_domain_operation(finished)
            except ValidationError:
                pass
            return

        limit = self._max_workers(task)
        executor = ThreadPoolExecutor(max_workers=limit)
        remaining = list(target_runs)
        pending = {}
        active_domain_dns = set()

        def domain_dn(target):
            if task.task_type != TaskRun.TaskType.DOMAIN_OPERATION:
                return ''
            snapshot = target.target_snapshot if isinstance(target.target_snapshot, dict) else {}
            value = snapshot.get('distinguished_name')
            return value.casefold() if isinstance(value, str) else ''

        def submit_available():
            # Do not put the entire task into the executor's unbounded pending queue.
            while len(pending) < limit and not lease_guard.is_set():
                target_index = next((
                    index for index, candidate in enumerate(remaining)
                    if not domain_dn(candidate) or domain_dn(candidate) not in active_domain_dns
                ), None)
                if target_index is None:
                    break
                target = remaining.pop(target_index)
                target_dn = domain_dn(target)
                if target_dn:
                    active_domain_dns.add(target_dn)
                future = executor.submit(
                    self._run_target, target, task.task_type, lease_guard, domain_context,
                )
                pending[future] = (target, target_dn)

        heartbeat_at = time.monotonic() + self._heartbeat_interval()
        try:
            submit_available()
            while pending:
                if lease_guard.is_set():
                    for future in pending:
                        future.cancel()
                timeout = (0.1 if lease_guard.is_set() else
                           min(0.1, max(0, heartbeat_at - time.monotonic())))
                completed, _ignored = wait(
                    pending,
                    timeout=timeout,
                    return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    _target, target_dn = pending.pop(future)
                    if target_dn:
                        active_domain_dns.discard(target_dn)
                    try:
                        future.result()
                    except CancelledError:
                        pass
                if not lease_guard.is_set() and time.monotonic() >= heartbeat_at:
                    with _database_guard():
                        renewed = renew_lease(task.pk, self.worker_id, self.lease_seconds)
                    if not renewed:
                        lease_guard.set()
                    heartbeat_at = time.monotonic() + self._heartbeat_interval()
                submit_available()
            if not lease_guard.is_set():
                try:
                    with _database_guard():
                        finished = finish_task(task.pk, self.worker_id)
                    if task.task_type == TaskRun.TaskType.DOMAIN_OPERATION:
                        aggregate_domain_operation(finished)
                except ValidationError:
                    # A lost or expired lease cannot aggregate or overwrite this task.
                    pass
        except BaseException:
            lease_guard.set()
            raise
        finally:
            # Python cannot kill a running thread. Stop writes/renewals, cancel
            # pending work, and wait for bounded protocol calls to actually end.
            executor.shutdown(wait=True, cancel_futures=True)
            if (
                task.task_type == TaskRun.TaskType.DOMAIN_OPERATION
                and domain_context is not None
                and domain_context.password
                and lease_guard.is_set()
            ):
                abort_password_domain_task(
                    task,
                    worker_id=self.worker_id,
                    claim_generation=domain_context.task_attempt,
                )
            domain_context = None

    @staticmethod
    def _stopped(stop_event, maintenance_stop=None):
        return stop_event.is_set() or (maintenance_stop is not None and maintenance_stop.is_set())

    def _maintenance_cycle(self, stop_event, maintenance_stop=None, *, schedule=True):
        """Run one maintenance pass without blocking target lease heartbeats."""
        if self._stopped(stop_event, maintenance_stop):
            return False
        if schedule:
            with _database_guard():
                cleanup_retained_logs(limit=self.threads * 4)
                if self._stopped(stop_event, maintenance_stop):
                    return False
                enqueue_due_schedules()
        if self._stopped(stop_event, maintenance_stop):
            return False
        from net.alerts.service import deliver_due_alerts, reconcile_terminal_targets
        from net.alerts.task_summaries import reconcile_terminal_tasks
        reconcile_terminal_targets(limit=max(1, self.threads * 8))
        if self._stopped(stop_event, maintenance_stop):
            return False
        reconcile_terminal_tasks(limit=max(1, self.threads * 8))
        if self._stopped(stop_event, maintenance_stop):
            return False
        return bool(deliver_due_alerts(limit=self.threads))

    def _maintenance_loop(self, stop_event, maintenance_stop):
        """Own and close one thread-local DB connection during a long task."""
        try:
            while not self._stopped(stop_event, maintenance_stop):
                try:
                    close_old_connections()
                    self._maintenance_cycle(stop_event, maintenance_stop)
                except OperationalError as exc:
                    if not is_sqlite_busy(exc):
                        logging.getLogger(__name__).error('Worker maintenance database operation failed.')
                except Exception:
                    logging.getLogger(__name__).error('Worker maintenance operation failed.')
                finally:
                    close_old_connections()
                    connections.close_all()
                if maintenance_stop.wait(self.poll_seconds):
                    break
        finally:
            close_old_connections()
            connections.close_all()

    def run_once(self, stop_event=None):
        """Claim at most one task while servicing schedules and alerts."""
        stop_event = stop_event if stop_event is not None else threading.Event()
        maintenance_stop = threading.Event()
        maintenance_thread = None
        handoffs = []
        task = None
        try:
            close_old_connections()
            if stop_event.is_set():
                return False
            with _database_guard():
                recover_expired_tasks()
                handoffs = cleanup_retained_logs(limit=self.threads * 4)
                if stop_event.is_set():
                    return False
                enqueue_due_schedules()
                if stop_event.is_set():
                    return False
                task = claim_next_task(self.worker_id, self.lease_seconds)
            if task is None:
                return self._maintenance_cycle(stop_event, schedule=False) or bool(handoffs)
            maintenance_thread = threading.Thread(
                target=self._maintenance_loop,
                args=(stop_event, maintenance_stop),
                name=f'task-maintenance-{self.worker_id}', daemon=True,
            )
            maintenance_thread.start()
            self._execute_claimed_task(task, stop_event)
            return True
        finally:
            try:
                maintenance_stop.set()
                if maintenance_thread is not None:
                    maintenance_thread.join()
                if task is not None and not stop_event.is_set():
                    # Preserve --once's final alert reconciliation without another
                    # schedule scan or a second task claim.
                    self._maintenance_cycle(stop_event, schedule=False)
            finally:
                try:
                    close_old_connections()
                finally:
                    connections.close_all()

    def run_forever(self, stop_event=None):
        """Poll until the optional event is set; portable to Windows and Linux."""
        stop_event = stop_event if stop_event is not None else threading.Event()
        while not stop_event.is_set():
            try:
                handled = self.run_once(stop_event)
            except OperationalError as exc:
                if not is_sqlite_busy(exc):
                    raise
                # _execute_claimed_task fences targets before propagating;
                # run_once closes connections. Expired work is recovered later.
                logging.getLogger(__name__).warning(
                    'SQLite 数据库正忙，Worker 将稍后重试；未完成任务按租约机制恢复。'
                )
                handled = False
            if not handled:
                stop_event.wait(self.poll_seconds)
