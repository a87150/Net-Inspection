"""Transactional creation and retry of secret-safe domain operation tasks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import time
from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone

from net.domain.secrets import PASSWORD_ACTIONS, store_operation_secret
from net.domain.validation import (
    _parse_rdn_components,
    validate_dn_within_base,
    validate_domain_action,
)
from net.models import (
    DomainOperation,
    Domain_Account,
    Domain_Computer,
    Domain_Controller_Config,
    TaskRun,
    TaskTargetRun,
)
from net.inspections.executor import _database_guard
from net.inspections.queue import is_sqlite_busy


_DOMAIN_TARGETS = {
    DomainOperation.ObjectType.ACCOUNT: (
        Domain_Account,
        TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
        'login_name',
    ),
    DomainOperation.ObjectType.COMPUTER: (
        Domain_Computer,
        TaskTargetRun.TargetType.DOMAIN_COMPUTER,
        'computer_name',
    ),
}


def _normalized_target_ids(target_ids):
    if isinstance(target_ids, (str, bytes)) or not isinstance(target_ids, Iterable):
        raise ValidationError({'target_ids': '目标必须是非空的 ID 列表。'})
    values = [str(value) for value in target_ids if value is not None]
    if not values or len(values) != len(set(values)):
        raise ValidationError({'target_ids': '目标必须是无重复的有效 ID 列表。'})
    return values


def _target_rows(object_type, target_ids):
    try:
        model, target_type, name_field = _DOMAIN_TARGETS[object_type]
    except KeyError:
        raise ValidationError({'object_type': '域控对象类型无效。'}) from None
    try:
        database_ids = [model._meta.pk.to_python(value) for value in target_ids]
    except (TypeError, ValueError, ValidationError):
        raise ValidationError({'target_ids': '目标 ID 格式无效。'}) from None
    rows = list(model.objects.select_for_update().filter(pk__in=database_ids).order_by('pk'))
    if len(rows) != len(database_ids):
        raise ValidationError({'target_ids': '一个或多个目标不存在。'})
    missing_dn = [row for row in rows if not row.distinguished_name]
    if missing_dn:
        raise ValidationError({'target_ids': '一个或多个目标缺少可解析的目录名称。'})
    return target_type, name_field, rows


def _target_snapshot(row, name_field):
    return {
        'id': str(row.pk),
        'distinguished_name': row.distinguished_name,
        'name': getattr(row, name_field),
    }


def _normalized_dn(value):
    try:
        return _parse_rdn_components(value)
    except ValidationError:
        raise ValidationError({'target_ids': '一个或多个目标包含无效目录名称。'}) from None


def _execution_scope_key(distinguished_name):
    """Return the stable per-DN database fencing key for a domain target."""
    normalized_dn = _normalized_dn(distinguished_name)
    serialized = json.dumps(normalized_dn, ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _execution_scope_keys(rows):
    """Reserve one global DN key and unique sibling keys within this task.

    The first target for a DN reserves the cross-task fencing key. Additional
    snapshots for the same DN are allowed in this task because the Worker
    serializes them by their immutable DN snapshot.
    """
    seen = {}
    keys = []
    for row in rows:
        base_key = _execution_scope_key(row.distinguished_name)
        ordinal = seen.get(base_key, 0)
        seen[base_key] = ordinal + 1
        if ordinal:
            base_key = hashlib.sha256(
                f'{base_key}:{ordinal}'.encode('ascii'),
            ).hexdigest()
        keys.append(base_key)
    return keys


def _reject_active_domain_target_overlap(target_type, rows):
    """Reject any active domain task that touches an object ID or canonical DN."""
    requested_ids = {str(row.pk) for row in rows}
    requested_dns = {_normalized_dn(row.distinguished_name) for row in rows}
    _reject_active_domain_dn_overlap(target_type, requested_dns, requested_ids)


def _reject_active_domain_dn_overlap(target_type, requested_dns, requested_ids=frozenset()):
    """Reject active tasks that touch immutable canonical directory names."""
    active_targets = list(
        TaskTargetRun.objects.select_for_update().filter(
            task__task_type=TaskRun.TaskType.DOMAIN_OPERATION,
            task__status__in=TaskRun.ACTIVE_STATUSES,
        ).order_by('task_id', 'pk')
    )
    for active_target in active_targets:
        if active_target.target_type == target_type and active_target.target_id in requested_ids:
            raise ValidationError({'target_ids': '一个或多个目标已有活动域控操作。'})
        snapshot = active_target.target_snapshot
        active_dn = snapshot.get('distinguished_name') if isinstance(snapshot, dict) else None
        if active_dn is None:
            raise ValidationError({'target_ids': '活动域控任务缺少安全的目录名称快照。'})
        try:
            overlaps_dn = _normalized_dn(active_dn) in requested_dns
        except ValidationError:
            raise ValidationError({'target_ids': '活动域控任务包含无效目录名称快照。'}) from None
        if overlaps_dn:
            raise ValidationError({'target_ids': '一个或多个目录名称已有活动域控操作。'})


def _create_user_target(parameters, base_dn):
    """Build the virtual target for a user that does not exist locally yet."""
    user_dn = validate_dn_within_base(parameters['user_dn'], base_dn)
    normalized = _normalized_dn(user_dn)
    canonical = json.dumps(normalized, ensure_ascii=True, separators=(',', ':'))
    target_id = str(uuid5(NAMESPACE_URL, f'domain-account-create:{canonical}'))
    snapshot = {
        'id': target_id,
        'distinguished_name': user_dn,
        'name': parameters['display_name'],
        'login_name': parameters['login_name'],
    }
    return target_id, user_dn, snapshot


def enqueue_domain_operation(
    *, requested_by, object_type, action, target_ids, parameters, password=None,
    _trusted_recovery_stage=None,
):
    """Retry only fully rolled-back local enqueue transactions, never LDAP writes."""
    if isinstance(target_ids, Iterable) and not isinstance(target_ids, (str, bytes)):
        target_ids = tuple(target_ids)
    for attempt in range(3):
        try:
            return _enqueue_domain_operation_once(
                requested_by=requested_by, object_type=object_type, action=action,
                target_ids=target_ids, parameters=parameters, password=password,
                _trusted_recovery_stage=_trusted_recovery_stage,
            )
        except OperationalError as exc:
            if not is_sqlite_busy(exc) or connection.in_atomic_block or attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))


def _enqueue_domain_operation_once(
    *, requested_by, object_type, action, target_ids, parameters, password=None,
    _trusted_recovery_stage=None,
):
    """Queue a validated operation, retaining passwords only in one-time storage."""
    if requested_by is None or getattr(requested_by, 'pk', None) is None:
        raise ValidationError({'requested_by': '必须提供已保存的操作发起人。'})
    if not isinstance(parameters, Mapping):
        raise ValidationError({'parameters': '操作参数必须是 JSON 对象。'})
    creating_user = (
        object_type == DomainOperation.ObjectType.ACCOUNT
        and action == DomainOperation.Action.CREATE_USER
    )
    if creating_user:
        if isinstance(target_ids, (str, bytes)) or not isinstance(target_ids, Iterable):
            raise ValidationError({'target_ids': '新增用户不接受既有账号目标。'})
        if list(target_ids):
            raise ValidationError({'target_ids': '新增用户不接受既有账号目标。'})
        target_ids = []
        if _trusted_recovery_stage not in (None, 'user_created_password_pending'):
            raise ValidationError({'operation_id': '新增用户恢复阶段无效。'})
    elif _trusted_recovery_stage is not None:
        raise ValidationError({'operation_id': '当前操作不接受恢复阶段。'})
    else:
        target_ids = _normalized_target_ids(target_ids)
    validated_parameters = validate_domain_action(
        object_type, action, {**parameters, 'target_count': 1 if creating_user else len(target_ids)},
    )
    requires_password = action in PASSWORD_ACTIONS
    if requires_password and (not isinstance(password, str) or not password):
        raise ValidationError({'password': '密码操作必须提交新的密码。'})
    if not requires_password and password is not None:
        raise ValidationError({'password': '当前操作不接受密码载荷。'})

    with _database_guard():
        with transaction.atomic():
            if connection.vendor == 'sqlite':
                # select_for_update is a no-op on SQLite. Take the cross-process
                # write reservation before SELECTs to avoid lock-upgrade deadlocks.
                Domain_Controller_Config.objects.filter(pk=1).update(name=F('name'))
            lock_row = (
                Domain_Controller_Config.objects.select_for_update().filter(pk=1).first()
            )
            if lock_row is None:
                raise ValidationError({'configuration': '未配置域控连接，无法安全创建域控操作任务。'})
            if creating_user:
                target_id, user_dn, target_snapshot = _create_user_target(
                    validated_parameters, lock_row.base_dn,
                )
                if _trusted_recovery_stage is not None:
                    target_snapshot['recovery_stage'] = _trusted_recovery_stage
                validated_parameters['user_dn'] = user_dn
                target_type = TaskTargetRun.TargetType.DOMAIN_ACCOUNT
                execution_scope_keys = [_execution_scope_key(user_dn)]
                _reject_active_domain_dn_overlap(target_type, {_normalized_dn(user_dn)})
                target_scope_snapshot = {'targets': [{
                    'target_type': target_type,
                    'target_id': target_id,
                }]}
                target_runs = [TaskTargetRun(
                    target_type=target_type,
                    target_id=target_id,
                    target_snapshot=target_snapshot,
                    execution_scope_key=execution_scope_keys[0],
                )]
                target_count = 1
            else:
                target_type, name_field, rows = _target_rows(object_type, target_ids)
                _reject_active_domain_target_overlap(target_type, rows)
                target_scope_snapshot = {
                    'targets': [
                        {'target_type': target_type, 'target_id': str(row.pk)}
                        for row in rows
                    ],
                }
                execution_scope_keys = _execution_scope_keys(rows)
                target_runs = [
                    TaskTargetRun(
                        target_type=target_type,
                        target_id=str(row.pk),
                        target_snapshot=_target_snapshot(row, name_field),
                        execution_scope_key=scope_key,
                    )
                    for row, scope_key in zip(rows, execution_scope_keys)
                ]
                target_count = len(rows)
            task = TaskRun(
                task_type=TaskRun.TaskType.DOMAIN_OPERATION,
                source=TaskRun.Source.MANUAL,
                available_at=timezone.now(),
                profile_snapshot={'object_type': object_type, 'action': action},
                parameters_snapshot=dict(validated_parameters),
                selected_items_snapshot=[],
                target_scope_snapshot=target_scope_snapshot,
                total_targets=target_count,
            )
            task.scope_key = TaskRun.build_scope_key(
                task_type=task.task_type,
                profile_id=f'{object_type}:{action}',
                target_scope_snapshot=target_scope_snapshot,
            )
            task.active_scope_key = task.scope_key
            if TaskRun.objects.filter(active_scope_key=task.scope_key).exists():
                raise ValidationError({'target_ids': '相同动作和目标范围已有活动任务。'})
            task.full_clean()
            try:
                with transaction.atomic():
                    task.save()
                    operation = DomainOperation(
                        action=action,
                        object_type=object_type,
                        requested_by=requested_by,
                        target_count=target_count,
                        parameter_summary=dict(validated_parameters),
                        task=task,
                    )
                    operation.full_clean()
                    for target in target_runs:
                        target.task = task
                        target.full_clean(validate_constraints=False)
                    operation.save()
                    for target in target_runs:
                        target.save()
                    if requires_password:
                        store_operation_secret(operation, {'password': password})
            except IntegrityError as exc:
                if TaskTargetRun.objects.filter(
                    execution_scope_key__in=execution_scope_keys,
                    status__in=TaskRun.ACTIVE_STATUSES,
                ).exists():
                    raise ValidationError({
                        'target_ids': '一个或多个目标已有活动域控操作。',
                    }) from exc
                if TaskRun.objects.filter(active_scope_key=task.scope_key).exists():
                    raise ValidationError({'target_ids': '相同动作和目标范围已有活动任务。'}) from exc
                raise
    return operation


def retry_failed_domain_operation(operation_id, *, requested_by, password=None):
    """Requeue only failed target snapshots; password actions require a new secret."""
    with transaction.atomic():
        operation = (
            DomainOperation.objects.select_for_update().select_related('task')
            .filter(pk=operation_id).first()
        )
        if operation is None or operation.task is None:
            raise ValidationError({'operation_id': '域控操作不存在。'})
        if operation.task.status not in TaskRun.AGGREGATED_TERMINAL_STATUSES:
            raise ValidationError({'operation_id': '只能重试已结束的域控操作。'})
        failed_targets = list(operation.task.target_runs.filter(
            status=TaskRun.Status.FAILED,
        ).order_by('target_id').values(
            'target_id', 'target_snapshot', 'result_snapshot',
        ))
        if not failed_targets:
            raise ValidationError({'operation_id': '没有可重试的失败目标。'})
        if operation.action == DomainOperation.Action.CREATE_USER:
            snapshot = failed_targets[0]['target_snapshot']
            if not isinstance(snapshot, dict):
                raise ValidationError({'operation_id': '失败目标缺少可重试的用户快照。'})
            parameters = {
                'user_dn': snapshot.get('distinguished_name'),
                'login_name': snapshot.get('login_name'),
                'display_name': snapshot.get('name'),
            }
            if len(failed_targets) != 1 or not all(isinstance(value, str) and value for value in parameters.values()):
                raise ValidationError({'operation_id': '失败目标缺少可重试的用户快照。'})
            result_snapshot = failed_targets[0]['result_snapshot']
            stage = result_snapshot.get('stage') if isinstance(result_snapshot, dict) else None
            if stage == 'manual_intervention_required':
                raise ValidationError({'operation_id': '新增用户结果需要人工核查，禁止自动重试。'})
            if stage == 'user_created_password_pending':
                stage_dn = result_snapshot.get('distinguished_name')
                try:
                    trusted_stage = (
                        result_snapshot.get('action') == DomainOperation.Action.CREATE_USER
                        and result_snapshot.get('success') is False
                        and isinstance(stage_dn, str)
                        and _normalized_dn(stage_dn) == _normalized_dn(parameters['user_dn'])
                    )
                except ValidationError:
                    trusted_stage = False
                if not trusted_stage:
                    raise ValidationError({
                        'operation_id': '新增用户恢复阶段无法验证，需要人工核查。',
                    })
                recovery_stage = stage
            elif stage in (None, ''):
                recovery_stage = None
            else:
                raise ValidationError({
                    'operation_id': '新增用户结果阶段无法验证，需要人工核查。',
                })
            target_ids = []
        else:
            recovery_stage = None
            target_ids = [target['target_id'] for target in failed_targets]
            parameters = dict(operation.parameter_summary)
    return enqueue_domain_operation(
        requested_by=requested_by,
        object_type=operation.object_type,
        action=operation.action,
        target_ids=target_ids,
        parameters=parameters,
        password=password,
        _trusted_recovery_stage=recovery_stage,
    )
