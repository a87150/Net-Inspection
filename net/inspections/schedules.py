"""Time-zone-aware calculations for database-backed schedules."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from threading import Lock

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from net.models import (
    ComputerAnalysisProfile,
    ComputerLogFile,
    InspectionProfile,
    PeopleSyncSource,
    Domain_Controller_Config,
    SecurityDevice,
    Network_Device,
    Schedule,
    Server,
    TaskRun,
)

from .queue import enqueue_task
from net.devices.pc.analysis_scope import enqueue_latest_analysis
from net.infrastructure.sanitization import sanitize


_ASSET_MODELS = {
    InspectionProfile.DeviceType.NETWORK_DEVICE: Network_Device,
    InspectionProfile.DeviceType.SERVER: Server,
    InspectionProfile.DeviceType.MONITOR: SecurityDevice,
}

# SQLite has no row-level ``SELECT ... FOR UPDATE`` semantics and concurrent
# writes fail immediately instead of waiting.  Development/test pollers are
# serialized in-process; production MySQL continues through the database row
# lock below, which also coordinates separate Worker processes and hosts.
_SQLITE_SCHEDULE_POLL_LOCK = Lock()


def target_rule_fields(device_type):
    """Only public registry fields that map directly to non-secret model fields."""
    from index.common.table_registry import get_table_definition
    key = {'server': 'servers', 'network_device': 'networks', 'monitor': 'monitors'}[device_type]
    model = _ASSET_MODELS[device_type]
    concrete = {field.name: field for field in model._meta.concrete_fields}
    return {field.source: (concrete[field.source], field.label)
            for field in get_table_definition(key).fields
            if field.filterable and field.source in concrete}


def _aware_reference(value, field_name):
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValidationError({field_name: '必须使用带时区的时间。'})
    return value


def next_run_at(schedule, after):
    """Return the first configured occurrence strictly after ``after``."""
    after = _aware_reference(after, 'after')
    if not isinstance(schedule, Schedule):
        raise ValidationError({'schedule': '必须提供有效的计划。'})

    if schedule.kind == Schedule.Kind.INTERVAL:
        if not schedule.interval_value or schedule.interval_unit not in {
            Schedule.IntervalUnit.MINUTES,
            Schedule.IntervalUnit.HOURS,
        }:
            raise ValidationError({'schedule': '间隔计划配置无效。'})
        if schedule.interval_unit == Schedule.IntervalUnit.MINUTES:
            return after + timedelta(minutes=schedule.interval_value)
        return after + timedelta(hours=schedule.interval_value)

    if schedule.kind == Schedule.Kind.DAILY:
        if schedule.daily_time is None:
            raise ValidationError({'schedule': '每日计划必须设置执行时间。'})
        local_timezone = timezone.get_default_timezone()
        local_after = timezone.localtime(after, local_timezone)
        candidate = timezone.make_aware(
            datetime.combine(local_after.date(), schedule.daily_time),
            local_timezone,
        )
        if candidate <= after:
            candidate = timezone.make_aware(
                datetime.combine(
                    local_after.date() + timedelta(days=1),
                    schedule.daily_time,
                ),
                local_timezone,
            )
        return candidate

    raise ValidationError({'schedule': '不支持的计划类型。'})


def _selected_target_ids(profile):
    if isinstance(profile, ComputerAnalysisProfile):
        return [
            str(primary_key)
            for primary_key in ComputerLogFile.objects.filter(
                retained=True,
            ).order_by('pk').values_list(
                'pk', flat=True,
            )
        ]

    if not isinstance(profile, InspectionProfile):
        raise ValidationError({'profile': '计划关联的配置无效。'})
    model = _ASSET_MODELS.get(profile.device_type)
    if model is None:
        raise ValidationError({'profile': '计划关联的设备类型无效。'})

    selector = profile.target_selector
    if not isinstance(selector, Mapping):
        raise ValidationError({'target_selector': '目标范围必须是 JSON 对象。'})
    selector = dict(selector)
    mode = selector.get('mode', 'all')
    queryset = model.objects.all()

    if mode == 'all':
        if set(selector) - {'mode'}:
            raise ValidationError({'target_selector': '全量目标范围不能包含筛选条件。'})
    elif mode == 'selected':
        target_ids = selector.get('target_ids')
        if (
            not isinstance(target_ids, list)
            or not target_ids
            or any(not isinstance(target_id, str) or not target_id for target_id in target_ids)
            or len(target_ids) != len(set(target_ids))
            or set(selector) != {'mode', 'target_ids'}
        ):
            raise ValidationError({'target_selector': '指定目标范围必须提供无重复的目标 ID 列表。'})
        try:
            normalized_ids = [model._meta.pk.to_python(target_id) for target_id in target_ids]
        except (TypeError, ValueError, ValidationError) as exc:
            raise ValidationError({'target_selector': '指定目标 ID 格式无效。'}) from exc
        queryset = queryset.filter(pk__in=normalized_ids)
        resolved_ids = {str(primary_key) for primary_key in queryset.values_list('pk', flat=True)}
        if resolved_ids != {str(primary_key) for primary_key in normalized_ids}:
            raise ValidationError({'target_selector': '指定目标不存在。'})
    elif mode == 'filtered':
        filters = selector.get('filters')
        if filters is None:
            filters = {
                field_name: value
                for field_name, value in selector.items()
                if field_name != 'mode'
            }
        elif set(selector) != {'mode', 'filters'}:
            raise ValidationError({'target_selector': '筛选目标范围必须提供 filters 对象。'})
        if not isinstance(filters, Mapping):
            raise ValidationError({'target_selector': '筛选目标范围必须提供 filters 对象。'})
        allowed_fields = {name: field for name, (field, label) in target_rule_fields(profile.device_type).items()}
        if not filters or any(
            not isinstance(field_name, str)
            or field_name not in allowed_fields
            or '__' in field_name
            for field_name in filters
        ):
            raise ValidationError({'target_selector': '筛选字段无效。'})
        normalized_filters = {}
        for field_name, value in filters.items():
            try:
                normalized_filters[field_name] = allowed_fields[field_name].clean(
                    value,
                    None,
                )
            except (ValidationError, ValueError, TypeError) as exc:
                raise ValidationError({
                    'target_selector': f'筛选字段 {field_name} 的值无效。',
                }) from exc
        queryset = queryset.filter(**normalized_filters)
    else:
        raise ValidationError({'target_selector': '目标范围模式无效。'})

    target_ids = [str(primary_key) for primary_key in queryset.order_by('pk').values_list('pk', flat=True)]
    if not target_ids:
        raise ValidationError({'target_selector': '目标范围没有可执行的目标。'})
    return target_ids


def _is_due(schedule, now):
    return schedule.next_run_at is None or schedule.next_run_at <= now


def _schedule_profile(schedule):
    profile = schedule.inspection_profile or schedule.analysis_profile or schedule.people_source or schedule.domain_config
    if profile is None:
        raise ValidationError({'schedule': '计划必须关联配置。'})
    return profile


def _has_active_schedule_scope(schedule):
    """Classify duplicate rejection from the durable queue scope, never profile-wide work."""
    try:
        profile = _schedule_profile(schedule)
    except ValidationError:
        return False
    if isinstance(profile, InspectionProfile):
        try:
            target_ids = _selected_target_ids(profile)
        except (ValidationError, TypeError, ValueError):
            return False
        scope = TaskRun.build_scope_key(
            task_type=TaskRun.TaskType.INSPECTION,
            profile_id=profile.pk,
            target_scope_snapshot={'targets': [
                {'target_type': profile.device_type, 'target_id': target_id}
                for target_id in target_ids
            ]},
        )
        return TaskRun.objects.filter(active_scope_key=scope).exists()
    # These scheduler-owned task types bind the Schedule onto the TaskRun, so
    # that relation is the durable identity available without reimplementing
    # their specialized enqueue snapshots.
    return TaskRun.objects.filter(
        schedule=schedule, active_scope_key__isnull=False,
    ).exists()


def _schedule_local_secrets(schedule):
    """Return only credentials reachable from this schedule's own binding."""
    try:
        profile = _schedule_profile(schedule)
    except ValidationError:
        return ()
    if isinstance(profile, PeopleSyncSource):
        credentials = profile.credentials if isinstance(profile.credentials, Mapping) else {}
        return tuple(value for value in credentials.values() if isinstance(value, str) and value)
    if isinstance(profile, Domain_Controller_Config):
        return tuple(value for value in (profile.bind_username, profile.bind_password)
                     if isinstance(value, str) and value)
    # Inspection and PC-analysis profile validation uses only public profile
    # fields.  PC source credentials are delegated to the OS credential store
    # and never appear in ValidationError messages.
    return ()


def _record_schedule_failure(schedule, now, exc):
    """Persist a safe failure only while this due attempt is still current."""
    try:
        blocked = _has_active_schedule_scope(schedule)
    except (ValidationError, TypeError, ValueError):
        blocked = False
    message = sanitize('；'.join(exc.messages), secrets=_schedule_local_secrets(schedule)).strip()[:500]
    values = {
        'last_schedule_attempt_at': now,
        'last_schedule_status': 'blocked' if blocked else 'error',
        'last_schedule_error': '等待活动任务结束。' if blocked else (message or '计划配置无效。'),
        'updated_at': now,
    }
    # The transaction that raised has rolled back.  Do not use its stale model
    # instance: another poll may already have enqueued this schedule and moved
    # next_run_at forward.
    Schedule.objects.filter(pk=schedule.pk).filter(
        Q(next_run_at__isnull=True) | Q(next_run_at__lte=now),
    ).filter(
        Q(last_schedule_attempt_at__isnull=True) | Q(last_schedule_attempt_at__lt=now),
    ).update(**values)


def _enqueue_due_schedules(now):
    due_schedule_ids = list(
        Schedule.objects.filter(is_enabled=True)
        .filter(Q(next_run_at__isnull=True) | Q(next_run_at__lte=now))
        .order_by('next_run_at', 'created_at', 'pk')
        .values_list('pk', flat=True)
    )
    tasks = []

    for schedule_id in due_schedule_ids:
        try:
            with transaction.atomic():
                schedule = (
                    Schedule.objects.select_for_update()
                    .select_related('inspection_profile', 'analysis_profile', 'people_source', 'domain_config')
                    .filter(pk=schedule_id)
                    .first()
                )
                if schedule is None or not schedule.is_enabled or not _is_due(schedule, now):
                    continue

                profile = _schedule_profile(schedule)
                overrides = {'available_at': now, 'schedule': schedule}
                if isinstance(profile, Domain_Controller_Config):
                    from net.domain.sync_tasks import enqueue_domain_sync
                    task = enqueue_domain_sync(schedule=schedule, available_at=now)
                elif isinstance(profile, PeopleSyncSource):
                    from net.people.tasks import enqueue_people_sync_task
                    task = enqueue_people_sync_task(schedule, available_at=now)
                elif isinstance(profile, ComputerAnalysisProfile):
                    task = enqueue_latest_analysis(
                        profile, TaskRun.Source.SCHEDULED, overrides=overrides,
                    )
                else:
                    task = enqueue_task(
                        profile,
                        _selected_target_ids(profile),
                        TaskRun.Source.SCHEDULED,
                        overrides=overrides,
                    )
                schedule.last_enqueued_at = now
                schedule.last_schedule_attempt_at = now
                schedule.last_schedule_status = 'queued'
                schedule.last_schedule_error = ''
                schedule.next_run_at = next_run_at(schedule, now)
                schedule.save(update_fields={
                    'last_enqueued_at', 'last_schedule_attempt_at',
                    'last_schedule_status', 'last_schedule_error',
                    'next_run_at', 'updated_at',
                })
                tasks.append(task)
        except ValidationError as exc:
            # A rolled-back atomic block leaves ``schedule`` stale; the helper
            # conditionally updates only an still-due, older attempt.
            _record_schedule_failure(schedule, now, exc)
            continue

    return tasks


def enqueue_due_schedules(now=None):
    """Atomically enqueue each currently due schedule at most once.

    A missing or overdue ``next_run_at`` produces one immediate task.  Its
    next occurrence is calculated from the actual enqueue time, so scheduler
    downtime never creates a backlog.  Duplicate active task scopes and
    invalid target ranges intentionally leave the schedule due for a later,
    retryable poll.
    """
    now = timezone.now() if now is None else now
    now = _aware_reference(now, 'now')
    if connection.vendor == 'sqlite':
        with _SQLITE_SCHEDULE_POLL_LOCK:
            return _enqueue_due_schedules(now)
    return _enqueue_due_schedules(now)
