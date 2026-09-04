"""Creation of immutable queue task snapshots and lease transitions."""

import json
from collections.abc import Mapping
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from net.models import (
    ComputerAnalysisProfile,
    ComputerLogFile,
    InspectionProfile,
    SecurityDevice,
    Network_Device,
    Server,
    TaskRun,
    TaskTargetRun,
)

from .state import (
    MAX_TASK_ATTEMPTS,
    bounded_error_summary,
    require_positive_int,
    save_target,
    save_task,
    target_failed,
    target_is_terminal,
)


_ASSET_SPECS = {
    TaskTargetRun.TargetType.NETWORK_DEVICE: (
        Network_Device,
        (
            'device_name', 'ip', 'device_type', 'model', 'vendor',
            'connection_type', 'port',
            'snmp_version', 'snmp_port', 'snmp_security_level',
            'snmp_username', 'snmp_auth_protocol', 'snmp_priv_protocol',
            'snmp_context_name', 'snmp_retries',
        ),
    ),
    TaskTargetRun.TargetType.SERVER: (
        Server,
        ('name', 'ip', 'server_type', 'os', 'port', 'api_url', 'verify_ssl'),
    ),
    TaskTargetRun.TargetType.MONITOR: (
        SecurityDevice,
        (
            'device_name', 'ip', 'device_type', 'model', 'vendor',
            'api_url', 'verify_ssl',
        ),
    ),
    TaskTargetRun.TargetType.COMPUTER_LOG: (
        ComputerLogFile,
        ('source_path', 'modified_at', 'content_hash', 'import_status', 'archived_path'),
    ),
}


def _task_context_for_profile(profile):
    from net.alerts.routing import snapshot_routing
    routing = snapshot_routing(profile)
    if isinstance(profile, InspectionProfile):
        return (
            TaskRun.TaskType.INSPECTION,
            profile.device_type,
            profile.selected_items,
            {
                'id': str(profile.pk),
                'name': profile.name,
                'device_type': profile.device_type,
                'selected_items': list(profile.selected_items),
                'target_selector': profile.target_selector,
                'timeout_seconds': profile.timeout_seconds,
                'concurrent_workers': profile.concurrent_workers,
                'alert_policy_mode': routing['mode'],
                'alert_routing': routing,
            },
        )
    if isinstance(profile, ComputerAnalysisProfile):
        return (
            TaskRun.TaskType.COMPUTER_ANALYSIS,
            TaskTargetRun.TargetType.COMPUTER_LOG,
            profile.analysis_items,
            {
                'id': str(profile.pk),
                'name': profile.name,
                'scan_directories': list(profile.scan_directories),
                'recursive': profile.recursive,
                'processed_directory': profile.processed_directory,
                'failed_directory': profile.failed_directory,
                'file_time_mode': profile.file_time_mode,
                'recent_days': profile.recent_days,
                'range_start_date': (
                    profile.range_start_date.isoformat()
                    if profile.range_start_date is not None else None
                ),
                'range_end_date': (
                    profile.range_end_date.isoformat()
                    if profile.range_end_date is not None else None
                ),
                'analysis_items': list(profile.analysis_items),
                'software_policy_path': profile.software_policy_path,
                'minimum_windows_release': profile.minimum_windows_release,
                'defender_update_max_days': profile.defender_update_max_days,
                'defender_scan_max_days': profile.defender_scan_max_days,
                'patch_max_days': profile.patch_max_days,
                'uptime_max_hours': profile.uptime_max_hours,
                'cpu_max_percent': profile.cpu_max_percent,
                'memory_max_percent': profile.memory_max_percent,
                'kms_servers': list(profile.kms_servers),
                'concurrent_workers': profile.concurrent_workers,
                'alert_policy_mode': routing['mode'],
                'alert_routing': routing,
            },
        )
    raise ValidationError({'profile': '必须提供已保存的巡检或日志分析配置。'})


def _normalize_target_ids(target_ids, expected_target_type):
    if isinstance(target_ids, (str, bytes)) or not hasattr(target_ids, '__iter__'):
        raise ValidationError({'target_ids': '目标必须是非空的 ID 列表。'})
    normalized = []
    for target in target_ids:
        if isinstance(target, Mapping):
            declared_type = target.get('target_type', expected_target_type)
            if declared_type != expected_target_type:
                raise ValidationError({
                    'target_type': '目标类型必须与任务配置的设备类型一致。',
                })
            target_id = target.get('target_id', target.get('id'))
            supplied_snapshot = target.get(
                'target_snapshot', target.get('snapshot'),
            )
            if supplied_snapshot is not None and not isinstance(
                supplied_snapshot, Mapping,
            ):
                raise ValidationError({'target_snapshot': '目标快照必须是 JSON 对象。'})
            if supplied_snapshot is not None:
                _json_object_copy(supplied_snapshot, 'target_snapshot')
        else:
            target_id = target
        normalized.append(str(target_id) if target_id is not None else '')
    if not normalized or any(not target_id for target_id in normalized):
        raise ValidationError({'target_ids': '至少需要一个有效目标。'})
    if len(normalized) != len(set(normalized)):
        raise ValidationError({'target_ids': '目标不能重复。'})
    return sorted(normalized)


def _target_snapshot(target, fields):
    snapshot = {'id': str(target.pk)}
    for field_name in fields:
        value = getattr(target, field_name)
        snapshot[field_name] = (
            value.isoformat() if hasattr(value, 'isoformat') else value
        )
    return snapshot


def _json_object_copy(value, field_name):
    if not isinstance(value, Mapping):
        raise ValidationError({field_name: '必须是 JSON 对象。'})
    try:
        return json.loads(json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        ))
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: '必须可序列化为 JSON。'}) from exc


def _validated_target_inputs(profile, target_ids, selected_items):
    task_type, target_type, profile_items, profile_snapshot = (
        _task_context_for_profile(profile)
    )
    if profile.pk is None:
        raise ValidationError({'profile': '配置必须先保存。'})
    if not profile.is_enabled:
        raise ValidationError({'profile': '已停用的配置不能创建任务。'})
    try:
        profile.full_clean()
    except ValidationError as exc:
        raise ValidationError({'profile': exc.message_dict}) from exc
    if any(item not in profile_items for item in selected_items):
        raise ValidationError({'selected_items': '只能选择配置已启用的检查项目。'})

    model, snapshot_fields = _ASSET_SPECS[target_type]
    target_id_list = _normalize_target_ids(target_ids, target_type)
    try:
        database_ids = [model._meta.pk.to_python(target_id) for target_id in target_id_list]
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValidationError({'target_ids': '目标 ID 格式无效。'}) from exc
    records = model.objects.in_bulk(database_ids)
    if len(records) != len(target_id_list):
        found = {str(primary_key) for primary_key in records}
        missing = [target_id for target_id in target_id_list if target_id not in found]
        raise ValidationError({'target_ids': f'目标不存在: {", ".join(missing)}'})
    records_by_id = {str(primary_key): record for primary_key, record in records.items()}
    targets = [
        (target_id, _target_snapshot(records_by_id[target_id], snapshot_fields))
        for target_id in target_id_list
    ]
    return task_type, target_type, profile_snapshot, targets


def _validate_target_before_enqueue(target):
    """Run every target contract that does not require the new task row yet."""
    target.clean_fields(exclude={'task'})
    target.clean()
    target.validate_unique(exclude={'task'})
    target.validate_constraints(exclude={'task'})


def enqueue_task(profile, target_ids, source, overrides=None, *, _frozen_parent=None):
    """Create one queued task with immutable profile and target snapshots."""
    if overrides is None:
        overrides = {}
    elif not isinstance(overrides, Mapping):
        raise ValidationError({'overrides': '任务参数必须是 JSON 对象。'})
    else:
        overrides = dict(overrides)
    unsupported = set(overrides) - {
        'selected_items', 'parameters', 'available_at', 'schedule',
    }
    if unsupported:
        raise ValidationError({'overrides': f'不支持的任务参数: {", ".join(sorted(unsupported))}'})
    if source not in TaskRun.Source.values:
        raise ValidationError({'source': '任务来源无效。'})
    _task_type, _target_type, profile_items, _profile_snapshot = (
        _task_context_for_profile(profile)
    )
    selected_items = overrides.get(
        'selected_items',
        profile_items,
    )
    if not isinstance(selected_items, list) or any(
        not isinstance(item, str) or not item.strip() for item in selected_items
    ) or len(selected_items) != len(set(selected_items)):
        raise ValidationError({'selected_items': '检查项目必须是无重复的非空字符串列表。'})
    parameters = _json_object_copy(overrides.get('parameters', {}), 'parameters')
    available_at = overrides.get('available_at', timezone.now())
    if not isinstance(available_at, datetime) or timezone.is_naive(available_at):
        raise ValidationError({'available_at': '可执行时间必须使用带时区的时间。'})
    schedule = overrides.get('schedule')

    task_type, target_type, profile_snapshot, targets = _validated_target_inputs(
        profile,
        target_ids,
        selected_items,
    )
    target_scope_snapshot = {
        # Child context is inherited below, never re-resolved from live policy.
        'targets': [
            {'target_type': target_type, 'target_id': target_id}
            for target_id, _snapshot in targets
        ],
    }
    task_kwargs = {
        'task_type': task_type,
        'source': source,
        'inspection_profile': profile if task_type == TaskRun.TaskType.INSPECTION else None,
        'analysis_profile': profile if task_type == TaskRun.TaskType.COMPUTER_ANALYSIS else None,
        'schedule': schedule,
        'available_at': available_at,
        'profile_snapshot': _json_object_copy(
            _frozen_parent.profile_snapshot if _frozen_parent is not None else profile_snapshot, 'profile_snapshot'),
        'parameters_snapshot': parameters,
        'selected_items_snapshot': list(selected_items),
        'target_scope_snapshot': _json_object_copy(
            target_scope_snapshot,
            'target_scope_snapshot',
        ),
        'total_targets': len(targets),
    }
    task = TaskRun(**task_kwargs)
    task.scope_key = TaskRun.build_scope_key(
        task_type=task.task_type,
        profile_id=profile.pk,
        target_scope_snapshot=target_scope_snapshot,
    )
    task.active_scope_key = task.scope_key
    if TaskRun.objects.filter(active_scope_key=task.scope_key).exists():
        raise ValidationError({'target_ids': '相同配置和目标范围已有活动任务。'})
    task.full_clean()

    target_runs = [
        TaskTargetRun(
            task=task,
            target_type=target_type,
            target_id=target_id,
            target_snapshot=snapshot,
        )
        for target_id, snapshot in targets
    ]
    for target in target_runs:
        _validate_target_before_enqueue(target)
    try:
        with transaction.atomic():
            task.save()
            for target in target_runs:
                target.save()
    except IntegrityError as exc:
        if TaskRun.objects.filter(active_scope_key=task.scope_key).exists():
            raise ValidationError({'target_ids': '相同配置和目标范围已有活动任务。'}) from exc
        raise
    return task


def enqueue_computer_scan_task(profile, source, overrides=None):
    """Queue one Worker-owned scan of an analysis profile's configured folders.

    A scan has one synthetic profile target.  This preserves normal task leases,
    progress and duplicate-scope protection without allowing a Web request or
    schedule poller to touch the filesystem.
    """
    if not isinstance(profile, ComputerAnalysisProfile):
        raise ValidationError({'profile': '日志扫描必须使用计算机日志分析配置。'})
    if profile.pk is None:
        raise ValidationError({'profile': '配置必须先保存。'})
    if not profile.is_enabled:
        raise ValidationError({'profile': '已停用的配置不能创建任务。'})
    try:
        profile.full_clean()
    except ValidationError as exc:
        raise ValidationError({'profile': exc.message_dict}) from exc
    if source not in TaskRun.Source.values:
        raise ValidationError({'source': '任务来源无效。'})
    if overrides is None:
        overrides = {}
    elif not isinstance(overrides, Mapping):
        raise ValidationError({'overrides': '任务参数必须是 JSON 对象。'})
    else:
        overrides = dict(overrides)
    unsupported = set(overrides) - {
        'selected_items', 'parameters', 'available_at', 'schedule',
    }
    if unsupported:
        raise ValidationError({
            'overrides': f'不支持的任务参数: {", ".join(sorted(unsupported))}',
        })

    selected_items = overrides.get('selected_items', profile.analysis_items)
    if (
        not isinstance(selected_items, list)
        or any(not isinstance(item, str) or not item.strip() for item in selected_items)
        or len(selected_items) != len(set(selected_items))
        or any(item not in profile.analysis_items for item in selected_items)
    ):
        raise ValidationError({'selected_items': '只能选择配置已启用的分析项目。'})
    parameters = _json_object_copy(overrides.get('parameters', {}), 'parameters')
    available_at = overrides.get('available_at', timezone.now())
    if not isinstance(available_at, datetime) or timezone.is_naive(available_at):
        raise ValidationError({'available_at': '可执行时间必须使用带时区的时间。'})
    schedule = overrides.get('schedule')
    _task_type, _target_type, _profile_items, profile_snapshot = (
        _task_context_for_profile(profile)
    )
    target_scope_snapshot = {
        'targets': [{
            'target_type': TaskTargetRun.TargetType.COMPUTER_SCAN,
            'target_id': str(profile.pk),
        }],
    }
    task = TaskRun(
        task_type=TaskRun.TaskType.COMPUTER_SCAN,
        source=source,
        analysis_profile=profile,
        schedule=schedule,
        available_at=available_at,
        profile_snapshot=_json_object_copy(profile_snapshot, 'profile_snapshot'),
        parameters_snapshot=parameters,
        selected_items_snapshot=list(selected_items),
        target_scope_snapshot=_json_object_copy(
            target_scope_snapshot, 'target_scope_snapshot',
        ),
        total_targets=1,
    )
    task.scope_key = TaskRun.build_scope_key(
        task_type=task.task_type,
        profile_id=profile.pk,
        target_scope_snapshot=target_scope_snapshot,
    )
    task.active_scope_key = task.scope_key
    if TaskRun.objects.filter(active_scope_key=task.scope_key).exists():
        raise ValidationError({'profile': '当前配置已有活动扫描任务。'})
    task.full_clean()
    target = TaskTargetRun(
        task=task,
        target_type=TaskTargetRun.TargetType.COMPUTER_SCAN,
        target_id=str(profile.pk),
        target_snapshot={
            'profile_id': str(profile.pk),
            'profile_name': profile.name,
        },
    )
    _validate_target_before_enqueue(target)
    try:
        with transaction.atomic():
            task.save()
            target.save()
    except IntegrityError as exc:
        if TaskRun.objects.filter(active_scope_key=task.scope_key).exists():
            raise ValidationError({'profile': '当前配置已有活动扫描任务。'}) from exc
        raise
    return task


def _queue_now(now):
    now = timezone.now() if now is None else now
    if not isinstance(now, datetime) or timezone.is_naive(now):
        raise ValidationError({'now': '必须使用带时区的时间。'})
    return now


def _recover_expired_locked(now):
    recovered = 0
    expired = list(
        TaskRun.objects.select_for_update()
        .filter(
            status=TaskRun.Status.RUNNING,
            lease_expires_at__lte=now,
        )
        .order_by('lease_expires_at', 'created_at', 'pk')
    )
    for task in expired:
        target_runs = list(
            task.target_runs.select_for_update().order_by('created_at', 'pk')
        )
        if target_runs and all(target_is_terminal(target) for target in target_runs):
            _finish_locked(task, now=now, target_runs=target_runs)
            _aggregate_domain_operation(task)
        elif task.attempt_count >= MAX_TASK_ATTEMPTS:
            for target in target_runs:
                if target_is_terminal(target):
                    continue
                target.status = TaskRun.Status.FAILED
                target.finished_at = now
                target.error_message = (
                    f'{target.error_message}\n' if target.error_message else ''
                ) + '任务租约过期且已达到最大重试次数。'
                save_target(target, {
                    'status', 'finished_at', 'error_message',
                })
            _finish_locked(task, now=now, target_runs=target_runs)
            _aggregate_domain_operation(task)
        else:
            for target in target_runs:
                if target.status == TaskRun.Status.RUNNING:
                    target.status = TaskRun.Status.QUEUED
                    target.started_at = None
                    save_target(target, {'status', 'started_at'})
            task.status = TaskRun.Status.QUEUED
            task.worker_id = ''
            task.lease_expires_at = None
            task.available_at = now
            save_task(task, {
                'status', 'worker_id', 'lease_expires_at', 'available_at',
            })
        recovered += 1
    return recovered


def _aggregate_domain_operation(task):
    """Keep a domain audit envelope terminal when lease recovery finishes its task."""
    if task.task_type != TaskRun.TaskType.DOMAIN_OPERATION:
        return None
    from net.domain.executor import aggregate_domain_operation

    return aggregate_domain_operation(task)


def claim_next_task(worker_id, lease_seconds, now=None, task_type=None):
    """Claim the oldest available task with an exclusive database lease."""
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValidationError({'worker_id': 'Worker 标识不能为空。'})
    require_positive_int(lease_seconds, 'lease_seconds')
    if task_type is not None and task_type not in TaskRun.TaskType.values:
        raise ValidationError({'task_type': '任务类型无效。'})
    now = _queue_now(now)
    with transaction.atomic():
        _recover_expired_locked(now)
        candidates = TaskRun.objects.select_for_update().filter(
                status=TaskRun.Status.QUEUED,
                available_at__lte=now,
            )
        if task_type is not None:
            candidates = candidates.filter(task_type=task_type)
        task = candidates.order_by('available_at', 'created_at', 'pk').first()
        if task is None:
            return None
        task.status = TaskRun.Status.RUNNING
        task.started_at = now
        task.worker_id = worker_id.strip()
        task.lease_expires_at = now + timedelta(seconds=lease_seconds)
        task.attempt_count += 1
        save_task(task, {
            'status', 'started_at', 'worker_id', 'lease_expires_at', 'attempt_count',
        })
        return task


def renew_lease(task_id, worker_id, lease_seconds):
    """Extend a live task lease only for its current owner."""
    if not isinstance(worker_id, str) or not worker_id.strip():
        return False
    require_positive_int(lease_seconds, 'lease_seconds')
    now = timezone.now()
    try:
        normalized_task_id = TaskRun._meta.pk.to_python(task_id)
    except (TypeError, ValueError, ValidationError):
        return False
    with transaction.atomic():
        task = TaskRun.objects.select_for_update().filter(pk=normalized_task_id).first()
        if (
            task is None
            or task.status != TaskRun.Status.RUNNING
            or task.worker_id != worker_id.strip()
            or task.lease_expires_at is None
            or task.lease_expires_at <= now
        ):
            return False
        task.lease_expires_at = now + timedelta(seconds=lease_seconds)
        save_task(task, {'lease_expires_at'})
        return True


def recover_expired_tasks(now=None):
    """Release expired leases or terminally fail tasks that exhausted retries."""
    now = _queue_now(now)
    with transaction.atomic():
        return _recover_expired_locked(now)


def _finish_locked(task, *, now, target_runs=None):
    if task.status in TaskRun.TERMINAL_STATUSES:
        return task
    if task.status != TaskRun.Status.RUNNING:
        raise ValidationError({'status': '只能汇总运行中的任务。'})
    target_runs = (
        list(task.target_runs.select_for_update().order_by('created_at', 'pk'))
        if target_runs is None else target_runs
    )
    for target in target_runs:
        target.full_clean()
    unfinished = [target for target in target_runs if not target_is_terminal(target)]
    if unfinished:
        raise ValidationError({'target_runs': '仍有目标尚未结束，不能汇总任务。'})
    successes = sum(
        target.status == TaskRun.Status.SUCCESS for target in target_runs
    )
    partials = sum(
        target.status == TaskRun.Status.PARTIAL for target in target_runs
    )
    failures = sum(target_failed(target) for target in target_runs)
    if successes == len(target_runs):
        status = TaskRun.Status.SUCCESS
    elif successes or partials:
        status = TaskRun.Status.PARTIAL
    else:
        status = TaskRun.Status.FAILED
    task.status = status
    task.progress = 100
    task.completed_targets = len(target_runs)
    task.successful_targets = successes
    task.failed_targets = failures
    task.finished_at = now
    task.lease_expires_at = None
    task.error_summary = bounded_error_summary(target_runs)
    save_task(task, {
        'status', 'progress', 'completed_targets', 'successful_targets',
        'failed_targets', 'finished_at', 'lease_expires_at', 'error_summary',
    })
    return task


def finish_task(task_id, worker_id):
    """Aggregate a task only for the Worker holding its current live lease."""
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValidationError({'worker_id': 'Worker 标识不能为空。'})
    now = timezone.now()
    with transaction.atomic():
        task = TaskRun.objects.select_for_update().filter(pk=task_id).first()
        if task is None:
            raise ValidationError({'task_id': '任务不存在。'})
        if (
            task.status != TaskRun.Status.RUNNING
            or task.worker_id != worker_id.strip()
            or task.lease_expires_at is None
            or task.lease_expires_at <= now
        ):
            raise ValidationError({
                'worker_id': '只有持有当前有效租约的 Worker 才能完成任务。',
            })
        return _finish_locked(task, now=now)
