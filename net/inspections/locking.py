"""Consistent parent-before-target row locks for task execution transactions."""
from net.models import TaskRun, TaskTargetRun


def locked_task_target(target_id):
    """Caller must be inside atomic(); immutable task_id is first read unlocked."""
    task_id = TaskTargetRun.objects.filter(pk=target_id).values_list('task_id', flat=True).first()
    if task_id is None:
        return None, None
    task = TaskRun.objects.select_for_update().filter(pk=task_id).first()
    if task is None:
        return None, None
    target = TaskTargetRun.objects.select_for_update().filter(pk=target_id, task_id=task_id).first()
    if target is not None:
        target.task = task
    return task, target
