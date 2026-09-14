"""Lease-fenced execution and aggregation for domain operation targets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from net.domain.client import DomainActionResult, DomainClient
from net.domain.secrets import PASSWORD_ACTIONS, consume_operation_secret
from net.domain.validation import _parse_rdn_components, validate_domain_action
from net.models import (
    DomainOperation, Domain_Account, Domain_Computer,
    Domain_Controller_Config, TaskRun, TaskTargetRun,
)
from net.infrastructure.sanitization import sanitize
from net.inspections.state import save_target

from net.inspections.executor import ExecutionOutcome, _begin_target, _database_guard, _has_live_lease, _target_id


@dataclass(frozen=True)
class DomainTaskContext:
    """Read-only, process-local state shared by one claimed domain task."""

    task_id: str
    task_attempt: int
    object_type: str
    action: str
    parameters: MappingProxyType
    password: str | None = None


PASSWORD_INTERRUPTED_ERROR = '密码操作已中断，请重新提交'


def prepare_domain_task_context(task, *, worker_id, claim_generation):
    """Consume a password once before Worker threads receive the task context."""
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValidationError('Worker 标识无效。')
    if not isinstance(claim_generation, int) or isinstance(claim_generation, bool):
        raise ValidationError('任务领取代次无效。')
    with transaction.atomic():
        claimed = TaskRun.objects.select_for_update().filter(pk=task.pk).first()
        now = timezone.now()
        if (
            claimed is None
            or claimed.task_type != TaskRun.TaskType.DOMAIN_OPERATION
            or claimed.status != TaskRun.Status.RUNNING
            or claimed.worker_id != worker_id.strip()
            or claimed.attempt_count != claim_generation
            or claimed.lease_expires_at is None
            or claimed.lease_expires_at <= now
        ):
            raise ValidationError('当前租约不能领取域控操作上下文。')
        operation = DomainOperation.objects.select_for_update().filter(task_id=claimed.pk).first()
        if operation is None:
            raise ValidationError('域控操作审计不存在。')
        parameters = validate_domain_action(
            operation.object_type, operation.action, claimed.parameters_snapshot,
        )
        password = None
        if operation.action in PASSWORD_ACTIONS:
            password = consume_operation_secret(operation.pk)['password']
        return DomainTaskContext(
            task_id=str(claimed.pk),
            task_attempt=claimed.attempt_count,
            object_type=operation.object_type,
            action=operation.action,
            parameters=MappingProxyType(dict(parameters)),
            password=password,
        )


def abort_password_domain_task(task, *, worker_id, claim_generation):
    """Terminally fail unfinished password targets after an interrupted consumption."""
    with _database_guard():
        with transaction.atomic():
            claimed = TaskRun.objects.select_for_update().filter(pk=task.pk).first()
            now = timezone.now()
            if (
                claimed is None
                or not _has_live_lease(claimed, worker_id, now)
                or claimed.attempt_count != claim_generation
            ):
                return False
            targets = list(
                claimed.target_runs.select_for_update().filter(
                    status__in=(TaskRun.Status.QUEUED, TaskRun.Status.RUNNING),
                ).order_by('created_at', 'pk')
            )
            for target in targets:
                target.status = TaskRun.Status.FAILED
                target.finished_at = now
                target.result_snapshot = {'interrupted': True}
                target.error_message = PASSWORD_INTERRUPTED_ERROR
                save_target(target, {
                    'status', 'finished_at', 'result_snapshot', 'error_message',
                })
    if not targets:
        return False
    from net.inspections.queue import finish_task

    try:
        finished = finish_task(task.pk, worker_id)
    except ValidationError:
        return False
    aggregate_domain_operation(finished)
    return True


def _redacted_error(value, context=None):
    secrets = (context.password,) if context is not None and context.password else ()
    message = sanitize(str(value or ''), secrets=secrets).strip()
    return '域控操作失败。' if not message or '[REDACTED]' in message else message[:2048]


def _same_dn(left, right):
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        return _parse_rdn_components(left) == _parse_rdn_components(right)
    except ValidationError:
        return False


def _update_local_mirror(target, context, details):
    if context is None or context.action == DomainOperation.Action.CREATE_USER:
        return ''
    model = {
        TaskTargetRun.TargetType.DOMAIN_ACCOUNT: Domain_Account,
        TaskTargetRun.TargetType.DOMAIN_COMPUTER: Domain_Computer,
    }.get(target.target_type)
    if model is None:
        return 'missing_waiting_for_sync'
    try:
        local = model.objects.select_for_update().filter(pk=target.target_id).first()
    except (TypeError, ValueError, ValidationError):
        local = None
    if local is None:
        return 'missing_waiting_for_sync'
    snapshot = target.target_snapshot if isinstance(target.target_snapshot, dict) else {}
    update_fields = []
    if context.action == DomainOperation.Action.MOVE_OU:
        changed_dn = details.get('distinguished_name') if isinstance(details, dict) else None
        changed_ou = details.get('ou') if isinstance(details, dict) else None
        if not isinstance(changed_dn, str) or not isinstance(changed_ou, str):
            return 'result_missing_waiting_for_sync'
        if not (
            _same_dn(local.distinguished_name, snapshot.get('distinguished_name'))
            or _same_dn(local.distinguished_name, changed_dn)
        ):
            return 'stale_waiting_for_sync'
        local.distinguished_name = changed_dn
        local.ou = changed_ou
        update_fields.extend(['distinguished_name', 'ou'])
    elif context.action in {DomainOperation.Action.ENABLE, DomainOperation.Action.DISABLE}:
        if not _same_dn(local.distinguished_name, snapshot.get('distinguished_name')):
            return 'stale_waiting_for_sync'
        local.is_active = context.action == DomainOperation.Action.ENABLE
        update_fields.append('is_active')
    if update_fields:
        local.save(update_fields=update_fields)
        return 'updated'
    return ''


def _safe_update_local_mirror(target, context, details):
    try:
        with transaction.atomic():
            return _update_local_mirror(target, context, details)
    except Exception:
        return 'update_failed_waiting_for_sync'


def _persist_domain_outcome(
    target_run_id, worker_id, *, success, error_message='', lease_guard=None,
    context=None, result_stage='', result_details=None,
):
    with _database_guard():
        with transaction.atomic():
            target = (
                TaskTargetRun.objects.select_for_update().select_related('task')
                .filter(pk=target_run_id).first()
            )
            if target is None:
                return ExecutionOutcome(str(target_run_id), TaskRun.Status.FAILED, stale=True)
            task = TaskRun.objects.select_for_update().filter(pk=target.task_id).first()
            now = timezone.now()
            if (
                task is None
                or not _has_live_lease(task, worker_id, now)
                or (lease_guard is not None and lease_guard.is_set())
                or target.status != TaskRun.Status.RUNNING
                or (context is not None and task.attempt_count != context.task_attempt)
            ):
                return ExecutionOutcome(str(target.pk), target.status, stale=True)
            target.status = TaskRun.Status.SUCCESS if success else TaskRun.Status.FAILED
            target.finished_at = now
            result_details = result_details if isinstance(result_details, Mapping) else {}
            safe_details = {
                key: value for key, value in result_details.items()
                if key in {'distinguished_name', 'ou'} and isinstance(value, str)
            }
            target.result_snapshot = {
                'action': context.action if context is not None else '',
                'success': bool(success),
            }
            if result_stage in {'user_created_password_pending', 'manual_intervention_required', 'completed'}:
                target.result_snapshot['stage'] = result_stage
            if safe_details:
                target.result_snapshot.update(safe_details)
            if success:
                mirror_status = _safe_update_local_mirror(target, context, safe_details)
                if mirror_status:
                    target.result_snapshot['mirror_status'] = mirror_status
            target.error_message = '' if success else _redacted_error(error_message, context)
            save_target(target, {'status', 'finished_at', 'result_snapshot', 'error_message'})
            return ExecutionOutcome(
                str(target.pk), target.status, error_message=target.error_message,
            )


def execute_domain_target(target_run, *, worker_id, lease_guard=None, domain_context=None):
    """Execute one immutable DN snapshot, then persist it behind the same lease."""
    target_run_id = _target_id(target_run)
    expected_attempt = domain_context.task_attempt if domain_context is not None else None
    started = _begin_target(
        target_run_id, worker_id, lease_guard, expected_task_attempt=expected_attempt,
    )
    if started is None:
        return ExecutionOutcome(target_run_id, TaskRun.Status.QUEUED, stale=True)
    if started.status in TaskRun.TERMINAL_STATUSES:
        return ExecutionOutcome(target_run_id, started.status)
    if lease_guard is not None and lease_guard.is_set():
        return ExecutionOutcome(target_run_id, started.status, stale=True)
    if domain_context is None:
        return _persist_domain_outcome(
            target_run_id, worker_id, success=False, error_message='域控任务上下文不可用。',
            lease_guard=lease_guard,
        )
    snapshot = started.target_snapshot if isinstance(started.target_snapshot, dict) else {}
    target_dn = snapshot.get('distinguished_name')
    if not isinstance(target_dn, str) or not target_dn:
        return _persist_domain_outcome(
            target_run_id, worker_id, success=False, error_message='域控目标缺少目录名称。',
            lease_guard=lease_guard, context=domain_context,
        )
    config = Domain_Controller_Config.objects.filter(pk=1).first()
    if config is None:
        return _persist_domain_outcome(
            target_run_id, worker_id, success=False, error_message='未配置域控连接。',
            lease_guard=lease_guard, context=domain_context,
        )
    try:
        result = DomainClient(config).execute(
            domain_context.object_type,
            domain_context.action,
            target_dn,
            dict(domain_context.parameters),
            password=domain_context.password,
            recovery_stage=snapshot.get('recovery_stage'),
        )
    except Exception:
        result = DomainActionResult(False, '域控操作失败。')
    return _persist_domain_outcome(
        target_run_id, worker_id, success=bool(result.success),
        error_message=result.error_message, lease_guard=lease_guard, context=domain_context,
        result_stage=result.stage, result_details=result.details,
    )


def persist_domain_execution_failure(target_run, *, worker_id, error, lease_guard=None, domain_context=None):
    """Persist an unexpected Worker failure without reflecting exception text."""
    return _persist_domain_outcome(
        _target_id(target_run), worker_id, success=False, error_message='域控目标执行失败。',
        lease_guard=lease_guard, context=domain_context,
    )


def aggregate_domain_operation(task):
    """Mirror terminal task aggregation onto its auditable operation envelope."""
    if task.status not in TaskRun.AGGREGATED_TERMINAL_STATUSES:
        return None
    with transaction.atomic():
        operation = DomainOperation.objects.select_for_update().filter(task_id=task.pk).first()
        if operation is None:
            return None
        operation.status = task.status
        operation.started_at = task.started_at
        operation.finished_at = task.finished_at
        operation.save(update_fields={'status', 'started_at', 'finished_at', 'updated_at'})
        return operation


def mark_domain_operation_running(task, *, worker_id, claim_generation):
    """Expose the claimed task state on its audit envelope before target threads run."""
    with transaction.atomic():
        claimed = TaskRun.objects.select_for_update().filter(pk=task.pk).first()
        now = timezone.now()
        if (
            claimed is None
            or not _has_live_lease(claimed, worker_id, now)
            or claimed.attempt_count != claim_generation
        ):
            return False
        operation = DomainOperation.objects.select_for_update().filter(task_id=claimed.pk).first()
        if operation is None:
            return False
        if operation.status == DomainOperation.Status.QUEUED:
            operation.status = DomainOperation.Status.RUNNING
            operation.started_at = claimed.started_at
            operation.save(update_fields={'status', 'started_at', 'updated_at'})
        return True
