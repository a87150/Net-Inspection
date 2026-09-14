"""Latest PC task statistics from persisted report projections, not log JSON."""
from django.db.models import Count, Q
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from net.inspections.issues import PROJECT_RULES
from net.models import ComputerAnalysis
from .result_query import computer_queryset


def _task_scope(task, records):
    if task.profile_snapshot.get('matching_mode') != 'people':
        return records, []
    from .people_result_query import PeopleResultRows
    joined = PeopleResultRows.from_roster(records, task.parameters_snapshot.get('personnel_roster', []))
    missing = joined.placeholders if task.status in {'success', 'partial', 'failed'} else []
    return joined.queryset, missing


@login_required
@require_GET
def analysis_problem_list(request, pk):
    from net.models import TaskRun
    from net.devices.pc.severity import grade_issue

    task = get_object_or_404(TaskRun, pk=pk, task_type=TaskRun.TaskType.COMPUTER_ANALYSIS)
    category = request.GET.get('category', '').strip()
    if not category or len(category) > 100 or '、' in category:
        raise Http404('未知的问题类型')
    records, missing = _task_scope(task, ComputerAnalysis.objects.filter(task_target__task_id=task.pk))
    if category == '人员缺少日志':
        page_obj = Paginator(missing, 20).get_page(request.GET.get('page'))
        return render(request, 'inspections/analysis_problem_rows.html', {
            'task': task, 'category': category, 'page_obj': page_obj,
            'rows': [{'record': row, 'findings': [{'title': '人员未匹配日志',
                      'description': row.summary, 'severity': 'warning', 'severity_label': '警告'}]}
                     for row in page_obj],
        })
    records = records.filter(
        Q(report_problem_types=category)
        | Q(report_problem_types__startswith=category + '、')
        | Q(report_problem_types__endswith='、' + category)
        | Q(report_problem_types__contains='、' + category + '、')
    ).select_related('computer').only(
        'pk', 'computer_id', 'computer__computer_name', 'exceptions',
    ).order_by('computer__computer_name', 'pk')
    page_obj = Paginator(records, 20).get_page(request.GET.get('page'))
    rows = []
    for record in page_obj:
        findings = []
        for raw in record.exceptions if isinstance(record.exceptions, list) else []:
            if not isinstance(raw, dict):
                continue
            issue = grade_issue(raw)
            if issue['category'] == category:
                findings.append({
                    'title': issue.get('问题类型') or '未命名问题',
                    'description': issue.get('详细问题') or '暂无具体说明，请查看分析详情。',
                    'severity': issue['severity'], 'severity_label': issue['severity_label'],
                })
        rows.append({'record': record, 'findings': findings})
    return render(request, 'inspections/analysis_problem_rows.html', {
        'task': task, 'category': category, 'page_obj': page_obj, 'rows': rows,
    })


def latest_analysis_statistics(task):
    levels = dict.fromkeys(('normal', 'info', 'warning', 'critical'), 0)
    categories = dict.fromkeys((category for category, _ in PROJECT_RULES['computers'].values()), 0)
    total = failed = 0
    if task is not None:
        records, missing = _task_scope(task, computer_queryset().filter(task_target__task_id=task.pk))
        counts = records.aggregate(total=Count('pk'), **{
            level: Count('pk', filter=Q(_report_level=level)) for level in levels
        })
        total = counts.pop('total')
        levels.update(counts)
        total += len(missing)
        levels['warning'] += len(missing)
        if missing:
            categories['人员缺少日志'] = len(missing)
        failed = task.target_runs.filter(status='failed').count()
        groups = records.order_by().values(
            'report_problem_types',
        ).annotate(count=Count('pk'))
        for group in groups:
            for category in set(filter(None, group['report_problem_types'].split('、'))):
                categories[category] = categories.get(category, 0) + group['count']
    return {
        'total': total, 'levels': levels, 'execution_failed': failed,
        'categories': [
            {'label': label, 'count': count, 'percentage': round(count * 100 / total, 1) if total else 0}
            for label, count in categories.items()
        ],
    }
