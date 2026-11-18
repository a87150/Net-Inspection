"""Detail payload for the PC log page."""
import json

from django.urls import reverse
from django.utils import timezone

from index.common.badges import status_badge
from index.common.detail_payload import (
    detail_payload, field_section, link_action, list_section,
)


def _stamp(value, fmt='%Y-%m-%d %H:%M:%S'):
    return timezone.localtime(value).strftime(fmt) if value else '-'


def computer_log_detail_payload(*, log, analyses):
    sections = [
        field_section('日志概览', [
            ('PC', (log.computer.computer_name if log.computer_id else '') or '未知'),
            ('采集时间', _stamp(log.collected_at)),
            ('平台', log.platform or '-'),
            ('内容哈希', log.content_hash),
        ]),
        list_section('分析历史', [
            {
                'label': _stamp(analysis.created_at),
                'meta': analysis.summary or '暂无摘要',
                'badge': status_badge(analysis.status, analysis.get_status_display()),
                'url': reverse('computer_analysis_detail', args=[analysis.pk]),
                'action': '查看详情',
            }
            for analysis in analyses
        ], empty='尚未分析。完成一次日志分析后，历史记录会显示在这里。'),
    ]
    return detail_payload(
        title='日志详情 #%s' % log.pk,
        eyebrow='PC 日志',
        description='日志内容只读；分析由手动或定时任务按每台 PC 最新日志执行。',
        actions=[link_action('返回日志列表', reverse('computer_log_list'))],
        sections=sections,
        disclosure={
            'heading': '已脱敏日志内容',
            'text': json.dumps(log.payload, ensure_ascii=False, indent=2, sort_keys=True),
        },
    )
