"""Snapshot-driven infrastructure collection and result persistence."""

from __future__ import annotations
from copy import copy, deepcopy

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
            'snmp_auth_password', 'snmp_priv_password', 'api_shared_secret',
        )
    elif target_type == TaskTargetRun.TargetType.SERVER:
        model, secret_fields = Server, ('username', 'password', 'api_token')
    elif target_type == TaskTargetRun.TargetType.MONITOR:
        model, secret_fields = SecurityDevice, ('api_username', 'api_password', 'api_token')
    else:
        raise ValueError(f'当前 Worker 不支持目标类型：{target_type}')

    connection_fields = {
        TaskTargetRun.TargetType.NETWORK_DEVICE: (
            'ip', 'port', 'vendor', 'connection_type', 'api_url', 'verify_ssl', 'snmp_version', 'snmp_port',
            'snmp_security_level', 'snmp_username', 'snmp_auth_protocol',
            'snmp_priv_protocol', 'snmp_context_name', 'snmp_retries',
        ),
        TaskTargetRun.TargetType.SERVER: ('ip', 'port', 'server_type', 'api_url', 'verify_ssl'),
        TaskTargetRun.TargetType.MONITOR: ('ip', 'vendor', 'device_type', 'api_url', 'verify_ssl'),
    }[target_type]
    # Fetch endpoint and credentials together: an enqueue/edit race must not
    # send newly saved credentials to the old endpoint in a frozen snapshot.
    current = model.objects.filter(pk=target.target_id).values(*connection_fields, *secret_fields).first()
    if current is None:
        raise LookupError('巡检目标已不存在，无法解析执行凭据。')
    if any(snapshot[field] != current[field] for field in connection_fields if field in snapshot):
        raise ValueError('设备连接配置在任务入队后已变更，请重新执行巡检。')
    snapshot.update({field: current[field] for field in secret_fields})
    if 'collection_settings' in snapshot:
        from net.devices.collection_profiles import attach_live_credentials
        from net.inspections.issues import DEVICE_PROJECTS
        effective = attach_live_credentials(DEVICE_PROJECTS[target_type], target.target_id, snapshot['collection_settings'])
        snapshot['collection_settings'] = effective
        if target_type == TaskTargetRun.TargetType.MONITOR:
            snapshot.update(effective.get('snmp', {}))
    return SimpleNamespace(**snapshot)


def _device_task(target, task):
    """Use per-target settings without changing the shared persisted task envelope."""
    effective = target.target_snapshot.get('collection_settings', {})
    if not effective:
        return task
    result = copy(task)
    result.profile_snapshot = deepcopy(task.profile_snapshot)
    if 'selected_items' in effective:
        needs_backup = (target.target_type == TaskTargetRun.TargetType.NETWORK_DEVICE
                        and target.target_snapshot.get('connection_type') != 'sangfor_api')
        result.selected_items_snapshot = [
            item for item in task.selected_items_snapshot
            if item in effective['selected_items'] or (item == 'config_info' and needs_backup)
        ]
    for key, source in [('issue_severity_overrides','severity_overrides'),('issue_thresholds','thresholds')]:
        result.profile_snapshot[key] = {**result.profile_snapshot.get(key,{}), **effective.get(source,{})}
    if 'alert_items' in effective:
        result.profile_snapshot['device_alert_items'] = effective['alert_items']
    return result


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
    task = _device_task(target, task)
    selection = {'selected_items': list(task.selected_items_snapshot)}
    if target.target_type == TaskTargetRun.TargetType.NETWORK_DEVICE:
        from net.devices.configuration_backups import latest_configuration_backup, backup_due
        saved_asset = Network_Device.objects.get(pk=target.target_id)
        backup = None
        if 'config_info' in selection['selected_items'] and not backup_due(saved_asset):
            backup = latest_configuration_backup(saved_asset)
            if backup is not None:
                selection['selected_items'].remove('config_info')
        if not selection['selected_items'] and backup is not None:
            # A reused backup proves neither online nor offline status. Verify
            # basic device access without fetching the configuration again.
            selection['selected_items'] = ['device_info']
        result = collect_network(asset, timeout, **selection)
        if backup is not None:
            result.data['config_info'] = _backup_metadata(backup)
            result.raw['config_info'] = _backup_metadata(backup)
        return result
    if target.target_type == TaskTargetRun.TargetType.SERVER:
        if str(getattr(asset, 'server_type', '')).lower() == 'windows':
            return collect_windows_http(asset, timeout, **selection)
        if not getattr(asset, 'username', '') or not getattr(asset, 'password', ''):
            return _missing_configuration('未配置 Linux SSH 账号和密码')
        return collect_linux_ssh(asset, timeout, **selection)
    if target.target_type == TaskTargetRun.TargetType.MONITOR:
        from net.devices.security.collector import collect_security
        return collect_security(asset, timeout, api_collector=collect_security_api, **selection)
    return _missing_configuration(f'当前 Worker 不支持目标类型：{target.target_type}')


def _backup_metadata(backup):
    return {'status': 'success', 'backup_id': str(backup.pk),
            'captured_at': backup.captured_at.isoformat(), 'scope': backup.scope,
            'sha256': backup.sha256, 'byte_size': backup.byte_size,
            'message': '原始配置已独立备份，仅管理员可以下载。'}


def _persist_configuration_backup(target, collection, now):
    """Keep raw configuration out of ordinary inspection JSON, even on failure."""
    from net.devices.configuration_backups import store_configuration_backup
    from net.data_exchange.adapters import UnsupportedConfiguration
    item = collection.data.get('config_info')
    if not isinstance(item, dict):
        return
    metadata = None
    if item.get('status') == 'success' and 'content' in item:
        model = {TaskTargetRun.TargetType.NETWORK_DEVICE: Network_Device,
                 TaskTargetRun.TargetType.MONITOR: SecurityDevice}.get(target.target_type)
        if model is not None:
            try:
                with transaction.atomic():
                    asset = model.objects.get(pk=target.target_id)
                    backup = store_configuration_backup(asset, item, task_target=target, captured_at=now)
                metadata = _backup_metadata(backup)
            except UnsupportedConfiguration:
                metadata = {'status': 'unsupported', 'message': '该厂商或接口尚不支持完整配置备份，局部配置不能用于整机恢复。'}
            except Exception:
                metadata = {'status': 'failed', 'message': '原始配置备份保存失败，请检查备份密钥和数据库；下次巡检会重试。'}
    if metadata is None:
        allowed = ('status', 'backup_id', 'captured_at', 'scope', 'sha256', 'byte_size', 'message')
        metadata = {key: item[key] for key in allowed if key in item}
    collection.data['config_info'] = metadata
    for key in list(collection.raw):
        if key == 'config_info' or key.partition(':')[2] == 'config_info':
            collection.raw[key] = metadata
    if metadata.get('status') != 'success' and collection.status == RecordStatus.SUCCESS:
        collection.status = RecordStatus.PARTIAL if len(collection.data) > 1 else RecordStatus.FAILED


def _record_status(collection):
    return (
        collection.status
        if collection.status in RecordStatus.values
        else RecordStatus.FAILED
    )


def _selected_details(task, collection):
    data = collection.data if isinstance(collection.data, dict) else {}
    selected = set(task.selected_items_snapshot or [])
    result = {
        key: value
        for key, value in data.items()
        if key in selected
    }
    raw = collection.raw if isinstance(collection.raw, dict) else {}
    errors = raw.get('collection_errors')
    if isinstance(errors, dict):
        selected_errors = {key: value for key, value in errors.items() if key in selected}
        if selected_errors:
            result['collection_errors'] = selected_errors
    return result


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
        if target.target_snapshot.get('connection_type') == 'sangfor_api':
            from net.devices.network.sangfor import STATUS_ENDPOINTS
            allowed = {'api:' + STATUS_ENDPOINTS[item][0] for item in task.selected_items_snapshot if item in STATUS_ENDPOINTS}
            return {key: value for key, value in raw.items() if key in allowed} if isinstance(raw, dict) else {}
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
            if key in allowed or key in {command for item, commands in target.target_snapshot.get('collection_settings', {}).get('commands', {}).items() if item in selected for command in commands} or (
                isinstance(key, str)
                and key.partition(':')[0] in {'snmp', 'ssh'}
                and key.partition(':')[2] in allowed
            )
        }
    elif target.target_type == TaskTargetRun.TargetType.MONITOR:
        aliases = SECURITY_FIELDS
    elif target.target_snapshot.get('server_type') == 'windows':
        result = selected_fields(raw, task.selected_items_snapshot, WINDOWS_FIELDS)
        if 'storage_status' in task.selected_items_snapshot and isinstance(raw, dict) and 'physical_disks' in raw:
            result['physical_disks'] = raw['physical_disks']
        if isinstance(raw, dict) and isinstance(raw.get('collection_errors'), dict):
            errors = {key: value for key, value in raw['collection_errors'].items() if key in task.selected_items_snapshot}
            if errors:
                result['collection_errors'] = errors
        return result
    else:
        aliases = LINUX_FIELDS
    return selected_fields(raw, task.selected_items_snapshot, aliases)


def _collection_report(target, task, collection, secrets):
    """Prepare selected, sanitized evidence and business findings without DB I/O."""
    status = _record_status(collection)
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
            thresholds=task.profile_snapshot.get('issue_thresholds', {}), message=collection.message,
            server_type=target.target_snapshot.get('server_type') if target.target_type == TaskTargetRun.TargetType.SERVER else None)
        enabled_alerts = task.profile_snapshot.get('device_alert_items')
        if enabled_alerts is not None:
            findings = [finding for finding in findings if finding.get('analysis_item') in enabled_alerts]
            normal_items = [item for item in normal_items if item in enabled_alerts]
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
    if details.get('issue_findings'):
        message = (message + '；' + '、'.join(f"{issue['category']}（{issue['severity_label']}）"
                   for issue in details['issue_findings']))[:4096]
    return status, details, raw_output, message


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

            task = _device_task(target, task)
            _persist_configuration_backup(target, collection, now)
            if 'config_info' in task.selected_items_snapshot and not secrets:
                # Also cover worker-level failure persistence, which did not
                # pass through execute_target's live credential resolution.
                secrets = configuration_secrets()
            status, details, raw_output, message = _collection_report(target, task, collection, secrets)
            record_model, error_model, result_type, asset_field = _record_spec(target)
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

                        inventory_data = collection.data
                        if isinstance(asset, Network_Device):
                            inventory_data = {**collection.data, 'device_info': details.get('device_info', {})}
                        refresh_asset_inventory(asset, inventory_data)
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
            'snmp_priv_password', 'api_username', 'api_password', 'api_token', 'api_shared_secret',
        ))
        if 'config_info' in _device_task(started, started.task).selected_items_snapshot:
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
