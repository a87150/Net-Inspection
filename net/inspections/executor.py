"""Snapshot-driven infrastructure collection and result persistence."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from types import SimpleNamespace
import threading

from django.db import connections, transaction
from django.utils import timezone

from net.models import (
    Error_Monitor,
    Error_Network_Device,
    Error_Server,
    SecurityDevice,
    Monitor_Inspection,
    Network_Device,
    Network_Device_Inspection,
    RecordStatus,
    Server,
    Server_Inspection,
    TaskRun,
    TaskTargetRun,
)
from net.devices.network.collector import collect_network
from net.devices.network.snmp import SNMP_ITEMS
from net.devices.network.ssh import NETWORK_COMMANDS
from net.devices.security.api import collect_security_api
from net.devices.server.linux_ssh import collect_linux_ssh
from net.devices.server.windows_http import collect_windows_http
from net.infrastructure.collection import CollectionResult
from net.infrastructure.sanitization import (
    sanitize, sanitize_configuration, sanitize_configuration_items, configuration_secrets,
)
from net.inspections.selection import LINUX_FIELDS, NETWORK_FIELDS, SECURITY_FIELDS, WINDOWS_FIELDS, selected_fields
from net.inspections.state import save_target
from net.inspections.locking import locked_task_target


_TERMINAL_STATUSES = TaskRun.TERMINAL_STATUSES
_SQLITE_EXECUTION_LOCK = threading.RLock()


@dataclass(frozen=True)
class ExecutionOutcome:
    """The persisted result of one target, without secret material."""

    target_id: str
    status: str
    result_type: str = ''
    result_id: str = ''
    error_message: str = ''
    stale: bool = False


def _target_id(target_run):
    return str(getattr(target_run, 'pk', target_run))


def _has_live_lease(task, worker_id, now):
    return (
        bool(worker_id)
        and task.status == TaskRun.Status.RUNNING
        and task.worker_id == worker_id
        and task.lease_expires_at is not None
        and task.lease_expires_at > now
    )


def _database_guard():
    """SQLite has database-wide writes; keep collection threads concurrent."""
    if connections['default'].vendor == 'sqlite':
        return _SQLITE_EXECUTION_LOCK
    return nullcontext()


def _begin_target(target_run_id, worker_id, lease_guard=None, *, expected_task_attempt=None):
    """Claim the target execution under the parent task's live lease."""
    with _database_guard():
        with transaction.atomic():
            task, target = locked_task_target(target_run_id)
            if target is None:
                return None
            now = timezone.now()
            if (task is None or not _has_live_lease(task, worker_id, now)
                    or (expected_task_attempt is not None and task.attempt_count != expected_task_attempt)
                    or (lease_guard is not None and lease_guard.is_set())):
                return None
            if target.status in _TERMINAL_STATUSES:
                return target
            if target.status != TaskRun.Status.QUEUED:
                return None
            target.status = TaskRun.Status.RUNNING
            target.started_at = now
            target.attempt_count += 1
            target.error_message = ''
            save_target(target, {'status', 'started_at', 'attempt_count', 'error_message'})
            return target


def _asset_context(target):
    """Combine immutable connection metadata with the minimal live secret set."""
    snapshot = dict(target.target_snapshot or {})
    target_type = target.target_type
    if target_type == TaskTargetRun.TargetType.NETWORK_DEVICE:
        model, secret_fields = Network_Device, (
            'username', 'password', 'snmp_community',
            'snmp_auth_password', 'snmp_priv_password',
        )
    elif target_type == TaskTargetRun.TargetType.SERVER:
        model, secret_fields = Server, ('username', 'password', 'api_token')
    elif target_type == TaskTargetRun.TargetType.MONITOR:
        model, secret_fields = SecurityDevice, ('api_username', 'api_password', 'api_token')
    else:
        raise ValueError(f'当前 Worker 不支持目标类型：{target_type}')

    secrets = model.objects.filter(pk=target.target_id).values(*secret_fields).first()
    if secrets is None:
        raise LookupError('巡检目标已不存在，无法解析执行凭据。')
    snapshot.update(secrets)
    return SimpleNamespace(**snapshot)


def _timeout_seconds(task):
    value = (task.profile_snapshot or {}).get('timeout_seconds', 60)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 60
    return max(1, min(3600, value))


def _missing_configuration(message):
    return CollectionResult(False, RecordStatus.FAILED, message=message)


def _collect(target, task, asset):
    timeout = _timeout_seconds(task)
    selection = {'selected_items': list(task.selected_items_snapshot)}
    if target.target_type == TaskTargetRun.TargetType.NETWORK_DEVICE:
        return collect_network(asset, timeout, **selection)
    if target.target_type == TaskTargetRun.TargetType.SERVER:
        if str(getattr(asset, 'server_type', '')).lower() == 'windows':
            return collect_windows_http(asset, timeout, **selection)
        if not getattr(asset, 'username', '') or not getattr(asset, 'password', ''):
            return _missing_configuration('未配置 Linux SSH 账号和密码')
        return collect_linux_ssh(asset, timeout, **selection)
    if target.target_type == TaskTargetRun.TargetType.MONITOR:
        if not getattr(asset, 'api_url', ''):
            return _missing_configuration('未配置安防设备 API 地址')
        return collect_security_api(asset, timeout, **selection)
    return _missing_configuration(f'当前 Worker 不支持目标类型：{target.target_type}')


def _record_status(collection):
    return (
        collection.status
        if collection.status in RecordStatus.values
        else RecordStatus.FAILED
    )


def _selected_details(task, collection):
    data = collection.data if isinstance(collection.data, dict) else {}
    selected = set(task.selected_items_snapshot or [])
    return {
        key: value
        for key, value in data.items()
        if key in selected
    }


def _record_spec(target):
    if target.target_type == TaskTargetRun.TargetType.NETWORK_DEVICE:
        return (
            Network_Device_Inspection,
            Error_Network_Device,
            'network_device_inspection',
            'device_id',
        )
    if target.target_type == TaskTargetRun.TargetType.SERVER:
        return Server_Inspection, Error_Server, 'server_inspection', 'server_id'
    if target.target_type == TaskTargetRun.TargetType.MONITOR:
        return Monitor_Inspection, Error_Monitor, 'monitor_inspection', 'monitor_id'
    raise ValueError(f'当前 Worker 不支持目标类型：{target.target_type}')


def _selected_raw(target, task, raw):
    if target.target_type == TaskTargetRun.TargetType.NETWORK_DEVICE:
        aliases = {item: tuple(command for commands in NETWORK_COMMANDS.values()
                               for key, command in zip(NETWORK_FIELDS, commands) if key == item)
                   for item in NETWORK_FIELDS}
        aliases['config_info'] = ('config_info',)
        selected = set(task.selected_items_snapshot)
        allowed = {
            key
            for item in selected
            for key in aliases.get(item, ())
        }
        allowed.update(item for item in selected if item in SNMP_ITEMS)
        if not isinstance(raw, dict):
            return {}
        return {
            key: value
            for key, value in raw.items()
            if key in allowed or (
                isinstance(key, str)
                and key.partition(':')[0] in {'snmp', 'ssh'}
                and key.partition(':')[2] in allowed
            )
        }
    elif target.target_type == TaskTargetRun.TargetType.MONITOR:
        aliases = SECURITY_FIELDS
    elif target.target_snapshot.get('server_type') == 'windows':
        aliases = WINDOWS_FIELDS
    else:
        aliases = LINUX_FIELDS
    return selected_fields(raw, task.selected_items_snapshot, aliases)


def _persist_collection(target_run_id, worker_id, collection, lease_guard=None, secrets=()):
    """Write one dynamic record only if this Worker still owns the lease."""
    with _database_guard():
        with transaction.atomic():
            task, target = locked_task_target(target_run_id)
            if target is None:
                return ExecutionOutcome(
                    str(target_run_id),
                    TaskRun.Status.FAILED,
                    stale=True,
                )
            now = timezone.now()
            if (
                (lease_guard is not None and lease_guard.is_set())
                or task is None
                or not _has_live_lease(task, worker_id, now)
                or target.status != TaskRun.Status.RUNNING
            ):
                return ExecutionOutcome(str(target.pk), target.status, stale=True)

            status = _record_status(collection)
            if 'config_info' in task.selected_items_snapshot and not secrets:
                # Also cover worker-level failure persistence, which did not
                # pass through execute_target's live credential resolution.
                secrets = configuration_secrets()
            if 'config_info' in task.selected_items_snapshot and 'config_info' not in collection.data:
                failed_config = {'status': 'failed', 'message': '配置采集未完成；请检查连接配置并重新执行。'}
                collection.data['config_info'] = failed_config
                collection.raw['config_info'] = failed_config
                if status == RecordStatus.SUCCESS:
                    status = RecordStatus.PARTIAL if len(collection.data) > 1 else RecordStatus.FAILED
            scrub = sanitize_configuration if 'config_info' in task.selected_items_snapshot else sanitize
            scrub_items = sanitize_configuration_items if 'config_info' in task.selected_items_snapshot else sanitize
            details = scrub_items(_selected_details(task, collection), secrets=secrets)
            from net.devices.pc.severity import grade_issue
            if task.profile_snapshot.get('issue_project'):
                from net.inspections.device_issues import evaluate_device_issues
                findings, normal_items = evaluate_device_issues(
                    task.profile_snapshot['issue_project'], task.selected_items_snapshot, details,
                    reachable=bool(collection.reachable), status=status,
                    overrides=task.profile_snapshot.get('issue_severity_overrides', {}),
                    thresholds=task.profile_snapshot.get('issue_thresholds', {}), message=collection.message)
                details['issue_findings'] = scrub_items(findings, secrets=secrets)
                details['normal_issue_items'] = normal_items
            elif status != RecordStatus.SUCCESS:
                issue = {'analysis_item': 'inspection_collection', '问题类型': '设备采集问题',
                         '详细问题': collection.message or '巡检未完整成功', 'severity': 'critical'}
                if status == RecordStatus.PARTIAL:
                    issue['data_state'] = 'partial'
                details['issue_findings'] = [grade_issue(issue, overrides=task.profile_snapshot.get('issue_severity_overrides', {}))]
                details['issue_findings'] = scrub_items(details['issue_findings'], secrets=secrets)
            raw_output = scrub_items(
                _selected_raw(target, task, collection.raw),
                secrets=secrets,
            )
            message = scrub(
                collection.message
                or (
                    '巡检成功'
                    if status == RecordStatus.SUCCESS
                    else '巡检未完整成功'
                ),
                secrets=secrets,
            )[:4096]
            record_model, error_model, result_type, asset_field = _record_spec(target)
            if details.get('issue_findings'):
                message = (message + '；' + '、'.join(f"{issue['category']}（{issue['severity_label']}）"
                           for issue in details['issue_findings']))[:4096]
            record = record_model.objects.create(
                **{asset_field: target.target_id},
                task_target=target,
                status=status,
                started_at=target.started_at or now,
                finished_at=now,
                summary=message,
                details=details,
                raw_output=raw_output,
                is_reachable=bool(collection.reachable),
                duration_ms=max(0, int(collection.duration_ms or 0)),
            )
            if status != RecordStatus.SUCCESS or any(issue['severity'] != 'info' for issue in details.get('issue_findings', [])):
                error_model.objects.create(
                    inspection=record,
                    error_message={
                        '采集状态': status,
                        '详细信息': message,
                        '问题': details.get('issue_findings', []),
                    },
                )
            target.status = status
            target.finished_at = now
            target.result_type = result_type
            target.result_id = str(record.pk)
            from net.inspections.result_storage import compact_result_snapshot
            target.result_snapshot = compact_result_snapshot(record, result_type=result_type)
            target.error_message = '' if status == RecordStatus.SUCCESS else message
            save_target(
                target,
                {
                    'status', 'finished_at', 'result_type', 'result_id',
                    'result_snapshot', 'error_message',
                },
            )
            if status in {RecordStatus.SUCCESS, RecordStatus.PARTIAL}:
                asset_model = {
                    TaskTargetRun.TargetType.NETWORK_DEVICE: Network_Device,
                    TaskTargetRun.TargetType.SERVER: Server,
                    TaskTargetRun.TargetType.MONITOR: SecurityDevice,
                }.get(target.target_type)
                if asset_model is not None:
                    asset = asset_model.objects.select_for_update().filter(pk=target.target_id).first()
                    if asset is not None:
                        from net.devices.inventory import refresh_asset_inventory

                        refresh_asset_inventory(asset, collection.data)
            if ((lease_guard is not None and lease_guard.is_set())
                    or not _has_live_lease(task, worker_id, timezone.now())):
                transaction.set_rollback(True)
                return ExecutionOutcome(str(target.pk), TaskRun.Status.RUNNING, stale=True)
            return ExecutionOutcome(
                str(target.pk),
                status,
                result_type=result_type,
                result_id=str(record.pk),
                error_message=target.error_message,
            )


def execute_target(target_run, *, worker_id, lease_guard=None):
    """Collect and persist one infrastructure target for the supplied lease owner."""
    target_run_id = _target_id(target_run)
    started = _begin_target(target_run_id, worker_id, lease_guard)
    if started is None:
        return ExecutionOutcome(target_run_id, TaskRun.Status.QUEUED, stale=True)
    if started.status in _TERMINAL_STATUSES:
        return ExecutionOutcome(target_run_id, started.status)
    if lease_guard is not None and lease_guard.is_set():
        return ExecutionOutcome(target_run_id, started.status, stale=True)
    secrets = ()
    try:
        asset = _asset_context(started)
        secrets = tuple(getattr(asset, field, '') for field in (
            'username', 'password', 'snmp_community', 'snmp_auth_password',
            'snmp_priv_password', 'api_username', 'api_password', 'api_token',
        ))
        if 'config_info' in started.task.selected_items_snapshot:
            secrets += configuration_secrets()
        collection = _collect(started, started.task, asset)
    except Exception as exc:  # Collector boundaries must never terminate sibling work.
        collection = CollectionResult(
            False,
            RecordStatus.FAILED,
            message=f'采集执行失败：{sanitize(str(exc), secrets=secrets)}',
        )
    outcome = _persist_collection(target_run_id, worker_id, collection, lease_guard, secrets)
    if not outcome.stale:
        from net.alerts.service import process_persisted_target

        # Alert state/outbox writes are short and contain no transport I/O.
        # Keep SQLite's single writer fence consistent with record persistence.
        with _database_guard():
            process_persisted_target(target_run_id)
    return outcome


def persist_execution_failure(target_run, *, worker_id, error, lease_guard=None):
    """Persist an unexpected executor failure without affecting sibling targets."""
    outcome = _persist_collection(
        _target_id(target_run),
        worker_id,
        CollectionResult(False, RecordStatus.FAILED, message=f'采集执行失败：{sanitize(str(error))}'),
        lease_guard,
    )
    if not outcome.stale:
        from net.alerts.service import process_persisted_target

        with _database_guard():
            process_persisted_target(target_run)
    return outcome
