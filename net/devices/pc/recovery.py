"""Explicit repair of legacy logs rejected solely for a missing timestamp."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from net.models import ComputerLogFile, TaskRun
from net.inspections.queue import enqueue_task
from .logs import _computer_defaults, _refresh_static_computer


@transaction.atomic
def recover_missing_timestamps(log_ids, profile, *, selected_items=None):
    if TaskRun.objects.filter(task_type__in=['computer_fetch', 'computer_analysis'],
                              status__in=TaskRun.ACTIVE_STATUSES).exists():
        raise ValidationError('请先等待 PC 获取和分析任务结束。')
    logs = list(ComputerLogFile.objects.select_for_update().filter(pk__in=log_ids).order_by('pk'))
    if len(logs) != len(set(log_ids)):
        raise ValidationError('恢复日志范围不完整。')
    restored = []
    for log in logs:
        if (log.import_status != 'failed' or log.parse_error != '日志时间缺失或格式无效。'
                or not isinstance(log.payload, dict) or '日志时间' in log.payload):
            raise ValidationError('所选日志不属于旧脚本时间缺失误判，未执行恢复。')
        name, collected, defaults = _computer_defaults(log.payload, log.modified_at)
        day = timezone.localdate(collected)
        if ComputerLogFile.objects.filter(computer__computer_name=name, collected_date=day,
                                           import_status='imported').exists():
            raise ValidationError('恢复范围与现有 PC 当日日志重复，未执行恢复。')
        os_name = str(defaults.get('os', '')).lower()
        platform = str(log.payload.get('平台') or log.payload.get('platform') or
                       ('macos' if 'mac' in os_name or 'darwin' in os_name else 'windows')).lower()
        if platform not in ('windows', 'macos'):
            raise ValidationError('日志平台无效，未执行恢复。')
        log.computer = _refresh_static_computer(log.payload, log.modified_at)
        log.collected_date, log.platform = day, platform
        log.import_status, log.parse_error = 'imported', ''
        # Do not alter evidence bytes/hash, payload, dates, paths or old task outcomes.
        log.save(update_fields=['computer', 'collected_date', 'platform', 'import_status', 'parse_error'])
        restored.append(log.pk)
    if not restored:
        raise ValidationError('没有需要恢复的日志。')
    return enqueue_task(profile, restored, 'manual', overrides={
        'selected_items': selected_items if selected_items is not None else profile.analysis_items,
    })
