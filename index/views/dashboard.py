from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import reverse

from net.services.dashboard_summary import (
    DASHBOARD_SUMMARY_ERROR_MESSAGE,
    build_asset_card_summaries,
    with_latest_status,
)
from net.services.task_summary import inspection_task_queryset, summarize_task


def _card_summary_values(summary, *, static=False, waiting_label=''):
    if summary.get('error'):
        return {'error': summary['error']}
    values = {
        **summary,
        'checked': (
            summary['normal']
            if static else summary['total'] - summary['unchecked']
        ),
        'bad': summary['abnormal'],
    }
    if waiting_label:
        values['note'] = (
            f"{waiting_label} {summary['unchecked']} 台"
            if summary['unchecked'] > 0 else ''
        )
    return values


def _domain_summary_values(account_summary, computer_summary):
    if account_summary.get('error') or computer_summary.get('error'):
        return {'error': DASHBOARD_SUMMARY_ERROR_MESSAGE}
    return {
        'account_total': account_summary['total'],
        'account_normal': account_summary['normal'],
        'account_abnormal': account_summary['abnormal'],
        'computer_total': computer_summary['total'],
        'computer_normal': computer_summary['normal'],
        'computer_abnormal': computer_summary['abnormal'],
    }


def index(request):
    summaries = {
        summary['key']: summary for summary in build_asset_card_summaries()
    }
    items = [
        {
            'key': 'people',
            'name': '人员',
            **_card_summary_values(summaries['people'], static=True),
            'total_label': '人员总数',
            'normal_label': '在职人数',
            'abnormal_label': '离职人数',
            'checked_label': '在职人数',
            'bad_label': '离职人数',
            'list_url': reverse('asset_list', args=['people']),
            'detail_url': reverse('people_statistics'),
            'detail_label': '人员统计',
        },
        {
            'key': 'domain',
            'name': '域控管理',
            **_domain_summary_values(
                summaries['domain_accounts'], summaries['domain_computers'],
            ),
            'target_url': reverse('domain_controller_settings'),
        },
        {
            'key': 'computers',
            'name': 'PC',
            **_card_summary_values(
                summaries['computers'], waiting_label='等待首次分析',
            ),
            'total_label': 'PC 总数',
            'normal_label': '正常设备',
            'abnormal_label': '异常设备',
            'last_run_label': '上次分析日期',
            'checked_label': '已分析设备',
            'bad_label': '异常设备',
            'list_url': reverse('asset_list', args=['computers']),
            'record_url': reverse('computer_analysis_list'),
            'record_label': '日志分析记录',
            'manual_action_label': '手动执行分析',
        },
        {
            'key': 'networks',
            'name': '网络设备',
            **_card_summary_values(
                summaries['networks'], waiting_label='等待首次巡检',
            ),
            'manual_action_label': '手动执行巡检',
            'normal_label': '巡检正常',
            'abnormal_label': '巡检异常',
            'last_run_label': '上次巡检日期',
            'checked_label': '已巡检设备',
            'bad_label': '巡检异常',
            'list_url': reverse('asset_list', args=['networks']),
            'record_url': reverse('record_list', args=['networks']),
            'record_label': '巡检记录',
        },
        {
            'key': 'servers',
            'name': '服务器',
            **_card_summary_values(
                summaries['servers'], waiting_label='等待首次巡检',
            ),
            'manual_action_label': '手动执行巡检',
            'normal_label': '巡检正常',
            'abnormal_label': '巡检异常',
            'last_run_label': '上次巡检日期',
            'checked_label': '已巡检设备',
            'bad_label': '巡检异常',
            'list_url': reverse('asset_list', args=['servers']),
            'record_url': reverse('record_list', args=['servers']),
            'record_label': '巡检记录',
        },
        {
            'key': 'monitors',
            'name': '安防设备',
            **_card_summary_values(
                summaries['monitors'], waiting_label='等待首次巡检',
            ),
            'manual_action_label': '手动执行巡检',
            'normal_label': '巡检正常',
            'abnormal_label': '巡检异常',
            'last_run_label': '上次巡检日期',
            'checked_label': '已巡检设备',
            'bad_label': '巡检异常',
            'list_url': reverse('asset_list', args=['monitors']),
            'record_url': reverse('record_list', args=['monitors']),
            'record_label': '巡检记录',
        },
    ]
    task_page = Paginator(inspection_task_queryset(), 10).get_page(
        request.GET.get('task_page'),
    )
    task_page.object_list = [summarize_task(task) for task in task_page.object_list]
    return render(request, 'index.html', {
        'items': items,
        'task_page': task_page,
    })
