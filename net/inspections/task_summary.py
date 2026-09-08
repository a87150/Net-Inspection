"""Home-page summaries for asset inspection execution tasks."""

from django.core.paginator import Paginator
from django.db.models import Case, When, Value, CharField, Count, Q, Max, QuerySet
from net.models import TaskRun, TaskTargetRun


def _outcome_expression(prefix='', task_prefix='task__'):
    def match(**values):
        return Q(**{prefix + key: value for key, value in values.items()})
    return Case(
        When(match(status__in=('queued', 'running')), then=Value('pending')),
        When(match(status='cancelled'), then=Value('cancelled')),
        When(match(status__in=('failed', 'partial')), then=Value('abnormal')),
        When(match(status='success'), then=Case(
            When(**{task_prefix + 'task_type': 'computer_fetch'}, then=Value('fetch_success')),
            When(match(result_snapshot__health_status='normal'), then=Value('normal')),
            When(match(result_snapshot__health_status='abnormal'), then=Value('abnormal')),
            When(match(result_snapshot__status='success'), then=Value('normal')),
            default=Value('abnormal'), output_field=CharField())),
        default=Value('pending'), output_field=CharField())


def _summary_counts(task_ids):
    rows = TaskTargetRun.objects.filter(task_id__in=task_ids).order_by().annotate(
        outcome=_outcome_expression()).values('task_id', 'outcome').annotate(count=Count('pk'))
    counts = {}
    for row in rows:
        counts.setdefault(row['task_id'], {})[row['outcome']] = row['count']
    return counts


def _prepare_summaries(tasks):
    tasks = list(tasks)
    counts = _summary_counts([task.pk for task in tasks]) if tasks else {}
    for task in tasks:
        task._summary_counts = counts.get(task.pk, {})
    return tasks


HOME_TASK_TYPES = (
    TaskRun.TaskType.INSPECTION,
    TaskRun.TaskType.COMPUTER_FETCH,
    TaskRun.TaskType.COMPUTER_ANALYSIS,
)

PROJECT_DEVICE_TYPES = {
    'networks': 'network_device',
    'servers': 'server',
    'monitors': 'monitor',
}


def inspection_task_queryset():
    """Return recent asset execution tasks with their target runs ready to summarize."""
    return (
        TaskRun.objects.filter(task_type__in=HOME_TASK_TYPES)
        .select_related('inspection_profile', 'analysis_profile')
        .defer('parameters_snapshot', 'target_scope_snapshot')
        .order_by('-created_at', '-pk')
    )


def project_task_queryset(kind):
    """Return execution tasks belonging to one record workspace."""
    queryset = TaskRun.objects.select_related(
        'inspection_profile', 'analysis_profile',
    ).defer('parameters_snapshot', 'target_scope_snapshot')
    if kind == 'computers':
        queryset = queryset.filter(task_type=TaskRun.TaskType.COMPUTER_ANALYSIS)
    else:
        try:
            device_type = PROJECT_DEVICE_TYPES[kind]
        except KeyError as exc:
            raise ValueError(f'Unknown project kind: {kind}') from exc
        queryset = queryset.filter(
            task_type=TaskRun.TaskType.INSPECTION,
            inspection_profile__device_type=device_type,
        )
    return queryset.order_by('-created_at', '-pk')


def _target_outcome(task, target):
    if target.status in {TaskRun.Status.QUEUED, TaskRun.Status.RUNNING}:
        return 'pending'
    if target.status == TaskRun.Status.CANCELLED:
        return 'cancelled'
    if target.status in {TaskRun.Status.FAILED, TaskRun.Status.PARTIAL}:
        return 'abnormal'
    if target.status == TaskRun.Status.SUCCESS:
        if task.task_type == TaskRun.TaskType.COMPUTER_FETCH:
            return 'fetch_success'
        if isinstance(target.result_snapshot, dict):
            health = target.result_snapshot.get('health_status')
            if health in {'normal', 'abnormal'}:
                return health
        result_status = (
            target.result_snapshot.get('status')
            if isinstance(target.result_snapshot, dict) else None
        )
        return 'normal' if result_status == TaskRun.Status.SUCCESS else 'abnormal'
    return 'pending'


def summarize_task(task) -> dict:
    """Build presentation-safe task counts without querying per target or task."""
    counts = {
        'normal': 0,
        'abnormal': 0,
        'pending': 0,
        'cancelled': 0,
        'fetch_success': 0,
    }
    if hasattr(task, '_summary_counts'):
        counts.update(task._summary_counts)
    elif 'target_runs' in getattr(task, '_prefetched_objects_cache', {}):
        for target in task.target_runs.all():
            counts[_target_outcome(task, target)] += 1
    else:
        counts.update(_summary_counts([task.pk]).get(task.pk, {}))
    materialized_count = sum(counts.values())
    missing_count = max(task.total_targets - materialized_count, 0)
    surplus_count = max(materialized_count - task.total_targets, 0)
    counts['pending'] += missing_count
    if missing_count:
        integrity_warning = (
            f'目标计数完整性警告：缺少 {missing_count} 条目标明细，已计入待处理'
        )
    elif surplus_count:
        integrity_warning = (
            f'目标计数完整性警告：多出 {surplus_count} 条目标明细，已按明细展示'
        )
    else:
        integrity_warning = ''

    profile = task.inspection_profile or task.analysis_profile
    profile_snapshot = task.profile_snapshot if isinstance(task.profile_snapshot, dict) else {}
    return {
        'task': task,
        'type_label': task.get_task_type_display(),
        'profile_label': profile.name if profile else profile_snapshot.get('name', '—'),
        'source_label': task.get_source_display(),
        'status_label': task.get_status_display(),
        'total': max(task.total_targets, materialized_count),
        'integrity_warning': integrity_warning,
        **counts,
    }


def build_project_task_metrics(tasks):
    """Summarize task/result health; queued and cancelled targets are not completed."""
    if isinstance(tasks, QuerySet):
        summary = tasks.aggregate(task_count=Count('pk'), latest_task_at=Max('created_at'))
        rows = TaskTargetRun.objects.filter(task_id__in=tasks.order_by().values('pk')).annotate(
            outcome=_outcome_expression())
        counts = rows.aggregate(normal_count=Count('pk', filter=Q(outcome='normal')),
                                abnormal_count=Count('pk', filter=Q(outcome='abnormal')))
        completed = counts['normal_count'] + counts['abnormal_count']
        return {**summary, **counts, 'completed_count': completed,
                'failure_rate': round(counts['abnormal_count'] * 100 / completed, 1) if completed else 0.0}
    normal_count = 0
    abnormal_count = 0
    for task in tasks:
        for target in task.target_runs.all():
            outcome = _target_outcome(task, target)
            normal_count += outcome == 'normal'
            abnormal_count += outcome == 'abnormal'
    completed_count = normal_count + abnormal_count
    return {
        'task_count': len(tasks),
        'completed_count': completed_count,
        'normal_count': normal_count,
        'abnormal_count': abnormal_count,
        'failure_rate': (
            round(abnormal_count * 100 / completed_count, 1)
            if completed_count else 0.0
        ),
        'latest_task_at': tasks[0].created_at if tasks else None,
    }


def project_workspace_context(request, kind):
    tasks = project_task_queryset(kind)
    metrics = build_project_task_metrics(tasks)
    page = Paginator(tasks, 7).get_page(request.GET.get('task_page'))
    rows = _prepare_summaries(page.object_list)
    page.object_list = [summarize_task(task) for task in rows]
    latest = rows[0] if page.number == 1 and rows else tasks.first()
    return {'task_metrics': metrics, 'task_page': page, 'latest_task': latest,
            'task_section_title': '日志分析任务' if kind == 'computers' else '巡检任务',
            'task_empty_title': '暂无日志分析任务' if kind == 'computers' else '暂无巡检任务'}


def summarize_tasks(tasks):
    return [summarize_task(task) for task in _prepare_summaries(tasks)]
