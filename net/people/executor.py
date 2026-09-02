"""Worker-only provider I/O and fenced publication of directory results."""

from django.db import transaction
from django.utils import timezone

from net.people.directory.base import DirectoryAdapterError
from net.people.directory.dingtalk import DingTalkDirectoryAdapter
from net.people.directory.feishu import FeishuDirectoryAdapter
from net.people.directory.sync import PeopleSyncError, preview_people_sync
from net.models import PeopleSyncSource, TaskRun, TaskTargetRun
from net.people.tasks import source_matches_task
from net.tasks.state import save_target
from net.tasks.executors.inspection import ExecutionOutcome, _begin_target, _database_guard, _has_live_lease, _target_id


def build_directory_adapter(source):
    return {'feishu': FeishuDirectoryAdapter, 'dingtalk': DingTalkDirectoryAdapter}[source.source_type](source)


def _publish(started, worker_id, result, error='', lease_guard=None):
    with _database_guard(), transaction.atomic():
        target = TaskTargetRun.objects.select_for_update().get(pk=started.pk)
        task = TaskRun.objects.select_for_update().get(pk=target.task_id)
        now = timezone.now()
        if (not _has_live_lease(task, worker_id, now)
                or (lease_guard is not None and lease_guard.is_set())
                or task.attempt_count != started.task.attempt_count
                or target.attempt_count != started.attempt_count
                or target.status != TaskRun.Status.RUNNING):
            return ExecutionOutcome(str(target.pk), target.status, stale=True)
        source = PeopleSyncSource.objects.select_for_update().get(pk=task.people_source_id)
        if not source_matches_task(source, task):
            result, error = {}, '人员目录来源配置已变更或停用，请重新预览。'
        target.status = TaskRun.Status.FAILED if error else TaskRun.Status.SUCCESS
        target.finished_at = now
        target.result_snapshot = result
        target.error_message = error
        target.result_type = task.task_type
        target.result_id = str(task.pk)
        save_target(target, {'status', 'finished_at', 'result_snapshot', 'error_message',
                             'result_type', 'result_id'})
        if not error and task.task_type == TaskRun.TaskType.PEOPLE_TEST:
            # Operational metadata is not a configuration change and must not
            # invalidate a simultaneously generated preview's updated_at binding.
            PeopleSyncSource.objects.filter(pk=source.pk).update(last_tested_at=now)
        return ExecutionOutcome(str(target.pk), target.status,
                                result_type=target.result_type, result_id=target.result_id,
                                error_message=error)


def execute_people_target(target_run, *, worker_id, lease_guard=None):
    expected_attempt = target_run.task.attempt_count if isinstance(target_run, TaskTargetRun) else None
    started = _begin_target(_target_id(target_run), worker_id, lease_guard,
                            expected_task_attempt=expected_attempt)
    if started is None:
        return ExecutionOutcome(_target_id(target_run), TaskRun.Status.QUEUED, stale=True)
    if started.status in TaskRun.TERMINAL_STATUSES:
        return ExecutionOutcome(str(started.pk), started.status)
    result, error = {}, ''
    try:
        source = PeopleSyncSource.objects.get(pk=started.task.people_source_id)
        if not source_matches_task(source, started.task):
            raise ValueError('changed configuration')
        adapter = build_directory_adapter(source)
        if adapter.fetch_configuration_identity != started.task.parameters_snapshot['fetch_configuration_identity']:
            raise ValueError('adapter configuration mismatch')
        if lease_guard is not None and lease_guard.is_set():
            return ExecutionOutcome(str(started.pk), started.status, stale=True)
        if started.task.task_type == TaskRun.TaskType.PEOPLE_TEST:
            adapter.test_connection()
            result = {'message': '连接测试成功。'}
        elif started.task.task_type == TaskRun.TaskType.PEOPLE_PREVIEW:
            preview = preview_people_sync(source, adapter)
            if not preview.is_valid or not preview.token:
                error = '人员目录预览失败：快照不完整、来源不匹配或存在人员冲突。'
                result = {'validation_messages': list(preview.validation_messages)}
            else:
                result = {'preview': preview.to_dict()}
        else:
            raise ValueError('unsupported operation')
    except (DirectoryAdapterError, PeopleSyncError):
        error = '人员目录接口调用或预览失败，请检查配置后重试。'
    except Exception:
        # Provider exception details may include credentials, tokens, or URLs.
        error = '人员目录操作失败，请检查来源配置后重试。'
    return _publish(started, worker_id, result, error, lease_guard)


def persist_people_failure(target_run, *, worker_id, error, lease_guard=None):
    # The original target was queued before the pool invocation; recover the
    # begun attempt, but retain the invocation's parent lease generation.
    started = TaskTargetRun.objects.select_related('task').get(pk=_target_id(target_run))
    if hasattr(target_run, 'task'):
        started.task = target_run.task
    return _publish(started, worker_id, {}, '人员目录执行或结果保存失败。', lease_guard)
