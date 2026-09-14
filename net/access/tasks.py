"""Queue durable, bounded access record synchronization tasks."""

import hashlib
import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from net.models.access import AccessRecordSource
from net.models.tasks import TaskRun, TaskTargetRun

MAX_SYNC_WINDOW = timedelta(days=7)


def source_configuration_identity(source):
    """Safe fingerprint; ciphertext is hashed but never copied into task data."""
    value = {
        'id': str(source.pk), 'platform': source.platform, 'api_version': source.api_version,
        'base_url': source.base_url, 'event_path': source.event_path,
        'authentication_mode': source.authentication_mode, 'authentication_name': source.authentication_name,
        'username': source.username, 'verify_ssl': source.verify_ssl,
        'credential_ciphertext': bytes(source.credential_ciphertext).hex(),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_matches_task(source, task):
    snapshot = task.parameters_snapshot if isinstance(task.parameters_snapshot, dict) else {}
    return source.is_enabled and snapshot.get('source_configuration_identity') == source_configuration_identity(source)


def enqueue_access_sync(source_ids, *, start_at=None, end_at=None, source=TaskRun.Source.MANUAL):
    if source != TaskRun.Source.MANUAL:
        raise ValidationError('门禁记录采集目前只支持管理员手动发起。')
    end_at = end_at or timezone.now()
    if timezone.is_naive(end_at):
        raise ValidationError('采集结束时间必须带时区。')
    tasks = []
    for source_id in dict.fromkeys(str(value) for value in source_ids):
        try:
            with transaction.atomic():
                platform = AccessRecordSource.objects.select_for_update().get(pk=source_id)
                if not platform.is_enabled:
                    raise ValidationError('已停用的平台来源不能采集。')
                window_start = start_at or platform.cursor_at or (end_at - timedelta(minutes=platform.default_lookback_minutes))
                if timezone.is_naive(window_start) or window_start >= end_at or end_at - window_start > MAX_SYNC_WINDOW:
                    raise ValidationError('单次采集范围必须大于零且不超过 7 天。')
                target_scope = {'targets': [{'target_type': TaskTargetRun.TargetType.ACCESS_SOURCE, 'target_id': str(platform.pk)}]}
                public = platform.public_data()
                task = TaskRun(
                    task_type=TaskRun.TaskType.ACCESS_SYNC, source=source, total_targets=1,
                    profile_snapshot=public, target_scope_snapshot=target_scope,
                    parameters_snapshot={
                        'concurrent_workers': 1, 'source_configuration_identity': source_configuration_identity(platform),
                        'window_start': window_start.isoformat(), 'window_end': end_at.isoformat(),
                    },
                )
                task.scope_key = TaskRun.build_scope_key(task_type=task.task_type, profile_id=platform.pk, target_scope_snapshot=target_scope)
                task.active_scope_key = task.scope_key
                task.full_clean(); task.save()
                target = TaskTargetRun(task=task, target_type=TaskTargetRun.TargetType.ACCESS_SOURCE, target_id=str(platform.pk), target_snapshot=public)
                target.full_clean(); target.save()
                tasks.append(task)
        except (AccessRecordSource.DoesNotExist, ValueError, TypeError):
            raise ValidationError('门禁平台来源无效。') from None
        except IntegrityError:
            raise ValidationError('当前平台已有活动采集任务，请等待完成。') from None
    return tasks
