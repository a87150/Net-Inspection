"""Home-page summaries for asset inspection execution tasks."""

from net.models import TaskRun


HOME_TASK_TYPES = (
    TaskRun.TaskType.INSPECTION,
    TaskRun.TaskType.COMPUTER_SCAN,
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
        .prefetch_related('target_runs')
        .order_by('-created_at', '-pk')
    )


def project_task_queryset(kind):
    """Return execution tasks belonging to one record workspace."""
    queryset = TaskRun.objects.select_related(
        'inspection_profile', 'analysis_profile',
    ).prefetch_related('target_runs')
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
        if task.task_type == TaskRun.TaskType.COMPUTER_SCAN:
            return 'scan_success'
        result_status = (
            target.result_snapshot.get('status')
            if isinstance(target.result_snapshot, dict) else None
        )
        return 'normal' if result_status == TaskRun.Status.SUCCESS else 'abnormal'
    return 'pending'


def summarize_task(task) -> dict:
    """Build presentation-safe task counts without querying per target or task."""
    targets = list(task.target_runs.all())
    counts = {
        'normal': 0,
        'abnormal': 0,
        'pending': 0,
        'cancelled': 0,
        'scan_success': 0,
    }
    for target in targets:
        counts[_target_outcome(task, target)] += 1
    materialized_count = len(targets)
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
