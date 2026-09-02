from collections import defaultdict
import logging

from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils.timezone import localdate, timedelta

from net.models import (
    ComputerAnalysis,
    Error_Computer,
    People,
    RecordStatus,
)
from net.services.dashboard_summary import (
    DASHBOARD_SUMMARY_ERROR_MESSAGE,
    build_asset_card_summaries,
    with_latest_status,
)
from net.services.task_summary import inspection_task_queryset, summarize_task


logger = logging.getLogger(__name__)


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
    first_person = None
    if not summaries['people'].get('error'):
        try:
            first_person = People.objects.order_by('name', 'employee_id', 'pk').first()
        except DatabaseError:
            logger.exception('Dashboard people detail query failed')
            summaries['people'] = {
                'key': 'people',
                'error': DASHBOARD_SUMMARY_ERROR_MESSAGE,
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
            'detail_url': (
                reverse('person_detail', args=[first_person.pk])
                if first_person else ''
            ),
            'detail_label': '人员详情',
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
            'record_label': '分析日志',
            'stats_url': reverse('detail', args=['computers']),
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


def detail(request, item):
    if item != 'computers':
        from django.http import Http404

        raise Http404('该类型尚未接入分析详情')

    today = localdate()
    start_date = today - timedelta(days=6)
    last_7_days = [start_date + timedelta(days=index) for index in range(7)]
    today_queryset = ComputerAnalysis.objects.filter(created_at__date=today)
    total_devices = today_queryset.values('computer_id').distinct().count()
    error_devices = today_queryset.filter(
        Q(errors__isnull=False) | ~Q(status=RecordStatus.SUCCESS),
    ).values('computer_id').distinct().count()

    daily_error_summary = defaultdict(lambda: defaultdict(int))
    errors = Error_Computer.objects.select_related('inspection').filter(
        inspection__created_at__date__gte=start_date,
        inspection__created_at__date__lte=today,
    )
    for error in errors:
        day = error.inspection.created_at.astimezone().date()
        daily_error_summary[day][error.error_type] += 1

    error_types = sorted({
        error_type
        for values in daily_error_summary.values()
        for error_type in values
    })
    daily_summary_list = []
    for day in last_7_days:
        day_queryset = ComputerAnalysis.objects.filter(created_at__date=day)
        daily_summary_list.append({
            'date': day,
            'values': [
                daily_error_summary[day].get(error_type, 0)
                for error_type in error_types
            ],
            'total_devices': day_queryset.values(
                'computer_id',
            ).distinct().count(),
            'error_devices': day_queryset.filter(
                Q(errors__isnull=False) | ~Q(status=RecordStatus.SUCCESS),
            ).values('computer_id').distinct().count(),
        })

    return render(request, 'detail.html', {
        'item': item,
        'total_devices': total_devices,
        'error_devices': error_devices,
        'normal_devices': total_devices - error_devices,
        'latest_time': ComputerAnalysis.objects.order_by(
            '-created_at',
        ).values_list('created_at', flat=True).first(),
        'error_types': error_types,
        'daily_summary_list': daily_summary_list,
    })
