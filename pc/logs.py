"""Read-only PC log navigation."""
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from index.common.table_payload import row_payloads, table_payload
from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition
from net.models import ComputerLogFile


def _log_actions(log):
    return [{
        'kind': 'link', 'label': '查看详情',
        'url': reverse('computer_log_detail', args=[log.pk]),
    }]


def computer_log_list(request, *, bulk=None):
    from .bulk_analysis import bulk_context
    definition = get_table_definition('computer_logs')
    rows, state = apply_table_filters(request, ComputerLogFile.objects.filter(retained=True), definition)
    page_obj = Paginator(rows, state['page_size']).get_page(request.GET.get('page'))
    return render(request, 'pc/log_list.html', {
        **(bulk if bulk is not None else bulk_context(request)),
        'table_definition': definition, 'table_state': state, 'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=['computer_logs']),
        'page_obj': page_obj,
        'records_bootstrap': table_payload(
            page_obj, definition,
            row_payloads(page_obj.object_list, definition, actions=_log_actions),
            extra={
                'endpoint': '/api/v1/computer-logs/', 'label': 'PC 日志',
                'region_label': 'PC 日志数据表',
            },
        ),
        'pagination_query': query_without_page(request),
    })


def computer_log_detail(request, pk):
    log = get_object_or_404(ComputerLogFile, pk=pk, retained=True)
    return render(request, 'pc/log_detail.html', {
        'log': log,
        'analyses': log.analyses.order_by('-created_at', '-pk'),
    })
