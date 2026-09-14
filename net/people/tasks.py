"""Database-only directory enqueue and session-owned preview confirmation."""

import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from net.people.directory.base import directory_source_configuration_identity
from net.people.directory.sync import PeopleSyncApplyError, SyncPreview, apply_people_sync
from net.models import PeopleSyncSource, TaskRun, TaskTargetRun
from net.inspections.state import save_task
from net.people.providers import get_provider_definition


PEOPLE_SESSION_TASKS = 'people_directory_task_ids'
PEOPLE_RUNNING_ACKS = 'people_directory_running_acks'
PEOPLE_TERMINAL_ACKS = 'people_directory_terminal_acks'
MAX_SESSION_TASKS = 20


def session_digest(session_key):
    if not session_key:
        return ''
    return salted_hmac('net.people.operation.session', session_key, algorithm='sha256').hexdigest()


def owns_people_task(task, session_key):
    digest = session_digest(session_key)
    owner = task.parameters_snapshot.get('owner_session_digest', '')
    return bool(digest and owner and constant_time_compare(owner, digest))


def remember_people_task(session, task_id):
    task_id = str(task_id)
    task_ids = [value for value in session.get(PEOPLE_SESSION_TASKS, []) if value != task_id]
    session[PEOPLE_SESSION_TASKS] = (task_ids + [task_id])[-MAX_SESSION_TASKS:]
    session.modified = True


def pending_people_tasks(session):
    valid_ids = []
    for value in session.get(PEOPLE_SESSION_TASKS, []):
        try:
            valid_ids.append(str(uuid.UUID(str(value))))
        except (TypeError, ValueError, AttributeError):
            continue
    tasks_by_id = {
        str(task.pk): task
        for task in TaskRun.objects.filter(
            pk__in=valid_ids,
            task_type__in=TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES,
        ).select_related('people_source')
    }
    owned = [
        tasks_by_id[task_id]
        for task_id in valid_ids
        if task_id in tasks_by_id
        and owns_people_task(tasks_by_id[task_id], session.session_key)
    ]
    normalized_ids = [str(task.pk) for task in owned]
    if normalized_ids != session.get(PEOPLE_SESSION_TASKS, []):
        session[PEOPLE_SESSION_TASKS] = normalized_ids
        session.modified = True
    return owned


def acknowledge_people_task(session, task_id, *, kind):
    task_id = str(task_id)
    key = PEOPLE_RUNNING_ACKS if kind == 'running' else PEOPLE_TERMINAL_ACKS
    values = [str(value) for value in session.get(key, []) if str(value) != task_id]
    session[key] = (values + [task_id])[-MAX_SESSION_TASKS:]
    session.modified = True


def source_matches_task(source, task):
    return (source.is_enabled and str(source.pk) == str(task.people_source_id)
            and directory_source_configuration_identity(source)
            == task.parameters_snapshot.get('fetch_configuration_identity'))


def enqueue_people_task(source_id, task_type, session_key):
    """One source target in the existing durable queue; never instantiate an adapter."""
    if task_type not in TaskRun.PEOPLE_TASK_TYPES or not session_key:
        raise ValidationError('人员目录操作或会话无效。')
    try:
        with transaction.atomic():
            source = PeopleSyncSource.objects.select_for_update().get(pk=source_id)
            if not source.is_enabled:
                raise ValidationError('已停用的来源不能创建任务。')
            source.full_clean()
            if (
                task_type == TaskRun.TaskType.PEOPLE_PREVIEW
                and not source.public_data()['connection_test_current']
            ):
                raise ValidationError('请先完成第 1 步“测试连接”，再预览数据。')
            scope = {'targets': [{'target_type': 'people_source', 'target_id': str(source.pk)}]}
            task = TaskRun(
                task_type=task_type, people_source=source, total_targets=1,
                profile_snapshot=source.public_data(), target_scope_snapshot=scope,
                parameters_snapshot={
                    'owner_session_digest': session_digest(session_key),
                    'concurrent_workers': 1,
                    'fetch_configuration_identity': directory_source_configuration_identity(source),
                },
            )
            task.scope_key = TaskRun.build_scope_key(
                task_type=task_type, profile_id=source.pk, target_scope_snapshot=scope,
            )
            task.active_scope_key = task.scope_key
            if TaskRun.objects.filter(active_scope_key=task.scope_key).exists():
                raise ValidationError('当前来源已有相同的活动操作，请等待完成。')
            task.full_clean()
            task.save()
            target = TaskTargetRun(task=task, target_type='people_source',
                                   target_id=str(source.pk), target_snapshot=source.public_data())
            target.full_clean()
            target.save()
            return task
    except (PeopleSyncSource.DoesNotExist, ValueError):
        raise ValidationError('人员目录来源无效。') from None
    except IntegrityError:
        raise ValidationError('当前来源已有相同的活动操作，请等待完成。') from None


def enqueue_people_sync_task(schedule, *, available_at):
    """Create one immutable, non-browser scheduled directory synchronization."""
    try:
        with transaction.atomic():
            locked_schedule = type(schedule).objects.select_for_update().select_related(
                'people_source',
            ).get(pk=schedule.pk)
            source = PeopleSyncSource.objects.select_for_update().get(
                pk=locked_schedule.people_source_id,
            )
            definition = get_provider_definition(source.source_type)
            if (
                not locked_schedule.is_enabled
                or locked_schedule.people_source_id != source.pk
                or not source.is_enabled
                or source.source_key != definition.source_key
                or not source.public_data()['connection_test_current']
            ):
                raise ValidationError('人员自动同步计划尚不具备执行条件。')
            source.full_clean()
            scope = {
                'targets': [{
                    'target_type': TaskTargetRun.TargetType.PEOPLE_SOURCE,
                    'target_id': str(source.pk),
                }],
            }
            task = TaskRun(
                task_type=TaskRun.TaskType.PEOPLE_SYNC,
                source=TaskRun.Source.SCHEDULED,
                people_source=source,
                schedule=locked_schedule,
                total_targets=1,
                available_at=available_at,
                profile_snapshot=source.public_data(),
                parameters_snapshot={
                    'concurrent_workers': 1,
                    'fetch_configuration_identity': directory_source_configuration_identity(source),
                    'schedule': {
                        'kind': locked_schedule.kind,
                        'interval_value': locked_schedule.interval_value,
                        'interval_unit': locked_schedule.interval_unit,
                        'daily_time': (
                            locked_schedule.daily_time.isoformat()
                            if locked_schedule.daily_time else None
                        ),
                    },
                },
                target_scope_snapshot=scope,
            )
            task.scope_key = TaskRun.build_scope_key(
                task_type=task.task_type,
                profile_id=source.pk,
                target_scope_snapshot=scope,
            )
            task.active_scope_key = task.scope_key
            if TaskRun.objects.filter(active_scope_key=task.scope_key).exists():
                raise ValidationError('当前平台已有活动的自动同步任务。')
            task.full_clean()
            task.save()
            target = TaskTargetRun(
                task=task,
                target_type=TaskTargetRun.TargetType.PEOPLE_SOURCE,
                target_id=str(source.pk),
                target_snapshot=source.public_data(),
            )
            target.full_clean()
            target.save()
            return task
    except (PeopleSyncSource.DoesNotExist, ValueError, TypeError, AttributeError):
        raise ValidationError('人员自动同步计划无效。') from None
    except IntegrityError:
        raise ValidationError('当前平台已有活动的自动同步任务。') from None


def apply_people_task(task_id, session_key, token):
    """Lock consumption and apply the saved, signed diff; no provider dependency."""
    with transaction.atomic():
        task = TaskRun.objects.select_for_update().get(pk=task_id)
        if not owns_people_task(task, session_key):
            raise PeopleSyncApplyError()
        if (task.task_type != TaskRun.TaskType.PEOPLE_PREVIEW
                or task.status != TaskRun.Status.SUCCESS or task.people_applied_at):
            raise PeopleSyncApplyError()
        source = PeopleSyncSource.objects.select_for_update().get(pk=task.people_source_id)
        if not source_matches_task(source, task):
            raise PeopleSyncApplyError()
        targets = list(task.target_runs.select_for_update())
        if len(targets) != 1 or targets[0].status != TaskRun.Status.SUCCESS:
            raise PeopleSyncApplyError()
        preview = SyncPreview.from_dict(targets[0].result_snapshot.get('preview'))
        if not isinstance(token, str) or not token or not constant_time_compare(token, preview.token):
            raise PeopleSyncApplyError()
        result = apply_people_sync(source, preview)
        task.people_applied_at = timezone.now()
        save_task(task, {'people_applied_at'})
        return result
