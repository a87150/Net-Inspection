"""Detail payload for the shared asset detail page (people, servers, networks, ...)."""
from django.urls import reverse
from django.utils import timezone

from index.common.detail_payload import (
    detail_payload, field_section, link_action, text_section,
)


def _latest_section(latest, latest_url):
    if latest is None:
        return text_section('最新执行结果', '尚无执行记录。')
    head = ' · '.join(part for part in (
        timezone.localtime(latest.created_at).strftime('%Y-%m-%d %H:%M:%S'),
        str(latest.execution_status or ''),
        str(latest.task_source or ''),
        '异常 %s' % latest.error_count,
    ) if part)
    body = '\n'.join(part for part in (
        head, str(latest.summary or ''), str(latest.key_metrics or ''),
    ) if part)
    return {
        'heading': '最新执行结果',
        'kind': 'text',
        'text': body,
        'link': {'label': '查看本次结果', 'url': latest_url} if latest_url else None,
    }


def asset_detail_payload(*, kind, item_name, asset, detail_fields, latest_record,
                         latest_url, history_url, back_url, manual_action_label,
                         can_administer):
    actions = []
    if can_administer and kind in {'networks', 'weakcurrent'}:
        actions.append(link_action(
            '配置备份历史',
            reverse('configuration_backup_list', args=[kind, asset.pk]),
        ))
        actions.append(link_action(
            '下载最近成功配置',
            reverse('configuration_download', args=[kind, asset.pk]),
            variant='success',
        ))
    if history_url:
        actions.append(link_action(
            '查看日志分析记录' if kind == 'computers' else '查看巡检记录',
            history_url, variant='primary',
        ))
    if can_administer and manual_action_label:
        actions.append(link_action(
            manual_action_label,
            '%s?task_modal=run&task_mode=selected&task_targets=%s' % (back_url, asset.pk),
            variant='primary',
        ))
    actions.append(link_action('返回列表', back_url))

    sections = [
        field_section('基础资料', [(item['label'], item['value']) for item in detail_fields]),
    ]
    if history_url:
        sections.append(_latest_section(latest_record, latest_url))

    return detail_payload(
        title=item_name + '详情', eyebrow='基础信息', description=str(asset),
        actions=actions, sections=sections,
    )


def configuration_notice(kind):
    """The credential warning shown above the detail card."""
    if kind == 'weakcurrent':
        return (
            '配置备份历史保存设备原文，下载文件可能包含账号、密码等敏感凭据，请妥善保管。'
            '历史页仅展示版本信息，不预览原文；下载已保存版本不会连接设备。'
            '弱电设备目前仅支持 Network 配置节的部分 API，不能提供整机备份；'
            '整机备份需明确对应型号的备份 API 支持。'
        )
    if kind == 'networks':
        return (
            '配置备份历史保存设备原文，下载文件可能包含账号、密码等敏感凭据，请妥善保管。'
            '历史页仅展示版本信息，不预览原文；下载已保存版本不会连接设备。'
            '网络设备每日巡检自动备份一次当前运行配置，不含证书、私钥或固件，不是整机固件备份。'
        )
    return ''
