"""Read-only PC log navigation."""
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition
from net.models import ComputerLogFile


def computer_log_list(request, *, bulk=None):
    from .bulk_analysis import bulk_context
    definition = get_table_definition('computer_logs')
    rows, state = apply_table_filters(request, ComputerLogFile.objects.filter(retained=True), definition)
    return render(request, 'devices/pc/log_list.html', {
        **(bulk if bulk is not None else bulk_context(request)),
        'table_definition': definition, 'table_state': state, 'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=['computer_logs']),
        'page_obj': Paginator(rows, state['page_size']).get_page(request.GET.get('page')),
        'pagination_query': query_without_page(request),
    })


def computer_log_detail(request, pk):
    log = get_object_or_404(ComputerLogFile, pk=pk, retained=True)
    return render(request, 'devices/pc/log_detail.html', {
        'log': log,
        'analyses': log.analyses.order_by('-created_at', '-pk'),
    })
