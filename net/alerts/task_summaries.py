"""One durable notification per completed task, after its target evidence settles."""
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from net.models import AlertChannel, AlertDelivery, AlertEvent, AlertPolicy, TaskRun
from net.infrastructure.sanitization import sanitize


TASK_TYPES = ('inspection', 'computer_analysis', 'computer_fetch')


def _scope(task):
    if task.task_type == 'inspection':
        return 'inspection_profile', str(task.inspection_profile_id)
    return 'computer_analysis_profile', str(task.analysis_profile_id)


def _examples(events):
    lines = []
    for event in events.select_related('target_run').order_by('occurred_at', 'pk').iterator(chunk_size=32):
        snapshot = event.target_run.target_snapshot if event.target_run_id else {}
        name = next((snapshot.get(key) for key in ('device_name', 'name', 'computer_name', 'ip') if snapshot.get(key)), event.target_id)
        for finding in event.findings or []:
            if not isinstance(finding, dict):
                continue
            title = sanitize(str(finding.get('title') or finding.get('key') or '问题'))[:100]
            detail = sanitize(str(finding.get('detail') or ''))[:160]
            lines.append(f'{sanitize(str(name))[:80]}：{title}' + (f'（{detail}）' if detail else ''))
            if len(lines) >= 10:
                return '\n'.join(lines) + '\n其余问题请查看任务详情。'
    return '\n'.join(lines) or '无'


def summary_data(task):
    targets = task.target_runs.all()
    ignored = Q(result_snapshot__has_key='ignored') & Q(result_snapshot__ignored=True)
    failed = Q(status__in=('failed', 'partial'))
    abnormal = failed | (Q(result_snapshot__has_key='alert_observation') & Q(result_snapshot__alert_observation__abnormal=True))
    legacy_abnormal = (Q(result_snapshot__has_key='health_status') & Q(result_snapshot__health_status='abnormal')) | Q(
        pk__in=task.alert_events.filter(event_type='abnormal').values('target_run_id'))
    abnormal |= ~Q(result_snapshot__has_key='alert_observation') & legacy_abnormal
    counts = targets.aggregate(
        total=Count('pk'),
        normal=Count('pk', filter=Q(status='success') & ~ignored & ~abnormal),
        abnormal=Count('pk', filter=abnormal & ~ignored),
        failed=Count('pk', filter=failed & ~ignored),
        cancelled=Count('pk', filter=Q(status='cancelled')),
        skipped=Count('pk', filter=ignored),
        info=Count('pk', filter=Q(result_snapshot__alert_observation__info=True) & ~ignored),
    )
    events = task.alert_events.exclude(event_type='summary')
    if task.task_type == 'computer_analysis':
        # Match the task page's personnel-first/log-first scope, including
        # personnel without logs and legacy out-of-scope evidence exclusions.
        from net.inspections.task_summary import summarize_task
        presentation = summarize_task(task)
        counts.update({key: presentation[key] for key in ('total', 'normal', 'abnormal', 'cancelled')})
        counts['skipped'] = presentation['ignored']
    counts['recovered'] = events.filter(event_type='recovery').values('target_run_id').distinct().count()
    start, end = task.started_at or task.created_at, task.finished_at or timezone.now()
    return {
        **counts, 'task_name': str(task.profile_snapshot.get('name') or task.pk),
        'task_type': task.get_task_type_display(), 'task_status': task.get_status_display(),
        'started_at': timezone.localtime(start).strftime('%Y-%m-%d %H:%M:%S'),
        'finished_at': timezone.localtime(end).strftime('%Y-%m-%d %H:%M:%S'),
        'duration': f'{max(0, int((end-start).total_seconds()))} 秒',
        'details_url': f'/tasks/{task.pk}/',
        'issues': _examples(events.filter(event_type='abnormal')) if events.filter(event_type='abnormal').exists()
                  else ('请查看任务详情中的异常或缺少日志人员。' if counts['abnormal'] else '无'),
        'recoveries': _examples(events.filter(event_type='recovery')),
    }


def process_task_summary(task_run):
    """Commit summary, outbox and completion marker together; no transport I/O."""
    from net.alerts.service import _policy_for_scope
    from net.alerts.templates import render_task_summary
    from net.inspections.executor import _database_guard

    task_id = getattr(task_run, 'pk', task_run)
    with _database_guard():
        try:
            with transaction.atomic():
                task = TaskRun.objects.select_for_update().get(pk=task_id)
                if task.task_type not in TASK_TYPES or task.status not in TaskRun.TERMINAL_STATUSES:
                    return None
                if task.alert_summary_processed_at is not None:
                    return AlertEvent.objects.filter(summary_task_id=task.pk).first()
                now = timezone.now()
                # Fair retry ordering also covers tasks waiting for evidence replay.
                TaskRun.objects.filter(pk=task.pk).update(alert_summary_attempted_at=now)
                if task.target_runs.exclude(status__in=TaskRun.TERMINAL_STATUSES).exists():
                    return None
                if task.target_runs.filter(alert_processed_at__isnull=True).exists():
                    return None
                if task.task_type == 'computer_fetch' and task.target_runs.filter(
                        result_type='computer_analysis_task').exists():
                    TaskRun.objects.filter(pk=task.pk).update(alert_summary_processed_at=now, alert_summary_error='')
                    return None
                profile_type, profile_id = _scope(task)
                routing = task.profile_snapshot.get('alert_routing')
                policy = (_policy_for_scope(profile_type, profile_id) if routing is None else
                          AlertPolicy.objects.filter(pk=routing.get('effective_policy_id')).first())
                data = summary_data(task)
                rendered = render_task_summary(data)
                data.update(message_title=rendered['title'], message_text=rendered['text'])
                event, created = AlertEvent.objects.get_or_create(summary_task=task, defaults={
                    'task': task, 'target_run': None, 'event_type': 'summary',
                    'profile_type': profile_type, 'profile_id': profile_id,
                    'target_type': 'task', 'target_id': str(task.pk), 'policy': policy,
                    'summary_data': data, 'summary': rendered['title'], 'findings': [],
                    'severity': 'warning' if data['abnormal'] else 'info', 'occurred_at': now,
                })
                if created:
                    channels = (AlertChannel.objects.filter(pk__in=routing.get('channel_ids', []), is_enabled=True)
                                if routing is not None else policy.effective_channels() if policy else [])
                    for channel in channels:
                        AlertDelivery.objects.get_or_create(event=event, channel=channel)
                    if not event.deliveries.exists():
                        event.status = AlertEvent.Status.FAILED
                        event.summary += '（未配置可用告警渠道）'
                        event.save(update_fields={'status', 'summary', 'updated_at'})
                TaskRun.objects.filter(pk=task.pk).update(alert_summary_processed_at=now, alert_summary_error='')
                return event
        except Exception as exc:
            TaskRun.objects.filter(pk=task_id, alert_summary_processed_at__isnull=True).update(
                alert_summary_attempted_at=timezone.now(),
                alert_summary_error=f'任务总结生成失败（{type(exc).__name__}），稍后自动重试。')
            return None


def reconcile_terminal_tasks(*, limit=100):
    """Recover completion/summary gaps without replaying migrated historical runs."""
    task_ids = list(TaskRun.objects.filter(task_type__in=TASK_TYPES,
        status__in=TaskRun.TERMINAL_STATUSES, alert_summary_processed_at__isnull=True).order_by(
            F('alert_summary_attempted_at').asc(nulls_first=True), 'finished_at', 'pk').values_list('pk', flat=True)[:limit])
    for task_id in task_ids:
        process_task_summary(task_id)
