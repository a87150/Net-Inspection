"""Queued full-directory synchronization with lease-fenced local publication."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from net.models import Domain_Controller_Config, TaskRun, TaskTargetRun
from net.domain.sync import fetch_domain_snapshot, apply_domain_snapshot
from net.inspections.executor import _begin_target, _database_guard, _has_live_lease, ExecutionOutcome
from net.inspections.state import save_target


def enqueue_domain_sync(*, schedule=None, available_at=None):
    with _database_guard(), transaction.atomic():
        config = Domain_Controller_Config.objects.select_for_update().filter(pk=1).first()
        if config is None or not config.host or not config.base_dn or not config.bind_username:
            raise ValidationError('请先保存完整的域控连接设置。')
        if schedule is not None and (schedule.domain_config_id != config.pk or not schedule.is_enabled):
            raise ValidationError('域控同步计划无效或已停用。')
        if TaskRun.objects.filter(task_type='domain_operation', status__in=TaskRun.ACTIVE_STATUSES).exists():
            raise ValidationError('有域控修改任务尚未结束，请完成后再同步。')
        if TaskRun.objects.filter(task_type='domain_sync', status__in=TaskRun.ACTIVE_STATUSES).exists():
            raise ValidationError('已有域控同步任务正在排队或执行，请勿重复提交。')
        task = TaskRun(task_type='domain_sync',
            source='scheduled' if schedule else 'manual', schedule=schedule,
            available_at=available_at or timezone.now(), total_targets=1,
            profile_snapshot={'name': '域控同步', 'host': config.host},
            parameters_snapshot={'config_version': config.updated_at.isoformat(), 'concurrent_workers': 1},
            target_scope_snapshot={'target_type': 'domain_config', 'target_ids': ['1']})
        task.scope_key = TaskRun.build_scope_key(task_type=task.task_type, profile_id=None,
                                               target_scope_snapshot=task.target_scope_snapshot)
        task.active_scope_key = task.scope_key
        task.full_clean()
        task.save()
        target = TaskTargetRun(task=task, target_type='domain_config', target_id='1',
                              target_snapshot={'name': config.name, 'host': config.host})
        target.full_clean()
        target.save()
        return task


def _publish(started, worker_id, snapshot=None, error='', lease_guard=None):
    with _database_guard(), transaction.atomic():
        task = TaskRun.objects.select_for_update().get(pk=started.task_id)
        target = TaskTargetRun.objects.select_for_update().get(pk=started.pk)
        now = timezone.now()
        if (not _has_live_lease(task, worker_id, now)
                or (lease_guard is not None and lease_guard.is_set())
                or task.attempt_count != started.task.attempt_count
                or target.attempt_count != started.attempt_count
                or target.status != 'running'):
            return ExecutionOutcome(str(target.pk), target.status, stale=True)
        config = Domain_Controller_Config.objects.select_for_update().filter(pk=1).first()
        if config is None or config.updated_at.isoformat() != task.parameters_snapshot['config_version']:
            error = '域控配置已变更，请重新创建同步任务。'
        result = {}
        if not error:
            try:
                with transaction.atomic():
                    counts = apply_domain_snapshot(snapshot)
                result = dict(zip(('accounts', 'computers', 'groups'), counts))
                result['message'] = '同步完成：{} 个账号，{} 台计算机，{} 个分组。'.format(*counts)
                if len(snapshot) > 3:
                    result['ous'] = len(snapshot[3])
                    result['message'] += f' {len(snapshot[3])} 个 OU。'
            except Exception:
                error = '域控同步数据保存失败，本次变更已回滚。'
        target.status = 'failed' if error else 'success'
        target.finished_at = timezone.now()
        target.result_snapshot = result
        target.error_message = error
        target.result_type = 'domain_sync'
        target.result_id = str(task.pk)
        save_target(target, {'status', 'finished_at', 'result_snapshot', 'error_message', 'result_type', 'result_id'})
        return ExecutionOutcome(str(target.pk), target.status, error_message=error)


def execute_domain_sync_target(target_run, *, worker_id, lease_guard=None):
    started = _begin_target(str(target_run.pk), worker_id, lease_guard,
                            expected_task_attempt=target_run.task.attempt_count)
    if started is None:
        return ExecutionOutcome(str(target_run.pk), 'queued', stale=True)
    if started.status in TaskRun.TERMINAL_STATUSES:
        return ExecutionOutcome(str(started.pk), started.status)
    snapshot, error = None, ''
    try:
        config = Domain_Controller_Config.objects.get(pk=1)
        if config.updated_at.isoformat() != started.task.parameters_snapshot['config_version']:
            raise ValidationError('changed configuration')
        snapshot = fetch_domain_snapshot(config)
    except Exception:
        error = '域控目录读取失败，请检查连接配置、认证和目录读取权限；本次未更新本地数据。'
    return _publish(started, worker_id, snapshot, error, lease_guard)


def persist_domain_sync_failure(target_run, *, worker_id, error, lease_guard=None):
    started = TaskTargetRun.objects.select_related('task').get(pk=target_run.pk)
    started.task = target_run.task
    return _publish(started, worker_id, error='域控同步执行失败。', lease_guard=lease_guard)
