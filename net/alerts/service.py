"""Transactional alert state changes and durable, lease-fenced fan-out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from net.alerts import build_alert_message, send_alert
from net.alerts.base import summarize
from net.models import AlertChannel, AlertDelivery, AlertEvent, AlertPolicy, AlertState, TaskRun, TaskTargetRun


RETRY_DELAY_SECONDS = 30
DELIVERY_LEASE_SECONDS = 30


@dataclass(frozen=True)
class Finding:
    """A normalized, persisted observation for one stable alert key."""

    key: str
    severity: str
    title: str
    detail: str
    state: str = 'abnormal'

    def as_dict(self):
        return {
            'key': self.key,
            'severity': self.severity,
            'title': self.title,
            'detail': self.detail,
            'state': self.state,
        }


def _finding_dict(value):
    if isinstance(value, Finding):
        value = value.as_dict()
    if not isinstance(value, dict):
        return None
    key = str(value.get('key') or '').strip()
    severity = str(value.get('severity') or '').strip()
    title = str(value.get('title') or '').strip()
    detail = str(value.get('detail') or '').strip()
    state = str(value.get('state') or 'abnormal').strip().lower()
    if not key or not severity or not title or state not in {'abnormal', 'normal', 'unknown'}:
        return None
    return {
        'key': key[:191],
        'severity': severity[:16],
        'title': title[:500],
        'detail': detail[:2000],
        'state': state,
    }


def _normalised_findings(findings):
    grouped = {}
    for value in findings or ():
        finding = _finding_dict(value)
        if finding is None:
            continue
        # An abnormal observation wins over a normal duplicate from the same
        # result, so ambiguous data can never create a false recovery.
        current = grouped.get(finding['key'])
        ranks = {'info': 0, 'warning': 1, 'critical': 2}
        if current is None or (current['state'] != 'abnormal' and finding['state'] == 'abnormal') or (
            current['state'] == finding['state'] and ranks.get(finding['severity'], 0) > ranks.get(current['severity'], 0)
        ):
            grouped[finding['key']] = finding
    return [grouped[key] for key in sorted(grouped)]


def _target_scope(target):
    task = target.task
    if task.task_type == TaskRun.TaskType.INSPECTION:
        profile_type, profile_id = 'inspection_profile', str(task.inspection_profile_id)
    else:
        profile_type, profile_id = 'computer_analysis_profile', str(task.analysis_profile_id)
    target_type, target_id = target.target_type, target.target_id
    if task.task_type == TaskRun.TaskType.COMPUTER_ANALYSIS and target.result_type == 'computer_analysis':
        from net.models import ComputerAnalysis

        analysis = ComputerAnalysis.objects.filter(pk=target.result_id).only('computer_id').first()
        if analysis is not None:
            target_type, target_id = 'computer', str(analysis.computer_id)
    return profile_type, profile_id, target_type, str(target_id)


def _policy_for_scope(profile_type, profile_id):
    lookup = {'inspection_profile_id': profile_id} if profile_type == 'inspection_profile' else {
        'analysis_profile_id': profile_id,
    }
    return AlertPolicy.objects.filter(**lookup).first() or AlertPolicy.objects.filter(
        default_slot=AlertPolicy.DEFAULT_SLOT,
    ).first()


def _event_defaults(target, event_type, findings, observed_at, policy):
    profile_type, profile_id, target_type, target_id = _target_scope(target)
    safe_findings = [{key: value for key, value in finding.items() if key != 'state'} for finding in findings]
    return {
        'task': target.task,
        'policy': policy,
        'profile_type': profile_type,
        'profile_id': profile_id,
        'target_type': target_type,
        'target_id': target_id,
        'event_type': event_type,
        'findings': safe_findings,
        'severity': max((item['severity'] for item in findings), default='',
                        key=lambda level: {'info': 0, 'warning': 1, 'critical': 2}.get(level, 0)),
        'summary': f'{len(findings)} merged finding(s)',
        'occurred_at': observed_at,
    }


def _locked_state(scope, finding_key):
    profile_type, profile_id, target_type, target_id = scope
    state = AlertState.objects.select_for_update().filter(
        profile_type=profile_type,
        profile_id=profile_id,
        target_type=target_type,
        target_id=target_id,
        finding_key=finding_key,
    ).first()
    if state is not None:
        return state, False
    try:
        with transaction.atomic():
            return AlertState.objects.create(
                profile_type=profile_type,
                profile_id=profile_id,
                target_type=target_type,
                target_id=target_id,
                finding_key=finding_key,
                status=AlertState.Status.NORMAL,
            ), True
    except IntegrityError:
        return AlertState.objects.select_for_update().get(
            profile_type=profile_type,
            profile_id=profile_id,
            target_type=target_type,
            target_id=target_id,
            finding_key=finding_key,
        ), False


def process_target_findings(target_run, findings) -> list[AlertEvent]:
    """Persist station-local abnormal/recovery evidence without delivery rows.

    This function only writes state, event, and outbox rows.  Transport I/O is
    deliberately deferred to :func:`deliver_event` after this transaction.
    """
    target_id = getattr(target_run, 'pk', target_run)
    normalized = _normalised_findings(findings)
    if not normalized:
        return []
    with transaction.atomic():
        target = TaskTargetRun.objects.select_for_update().select_related('task').get(pk=target_id)
        observed_at = target.finished_at or timezone.now()
        scope = _target_scope(target)
        abnormal, recovered = [], []
        state_groups = {'abnormal': [], 'recovery': []}
        for finding in normalized:
            if finding['state'] == 'unknown':
                continue
            state, created = _locked_state(scope, finding['key'])
            if not created and state.last_seen_at and observed_at <= state.last_seen_at:
                continue
            was_abnormal = state.status == AlertState.Status.ABNORMAL
            state.last_seen_at = observed_at
            state.finding_snapshot = {key: value for key, value in finding.items() if key != 'state'}
            if finding['state'] == 'abnormal':
                state.status = AlertState.Status.ABNORMAL
                state.last_abnormal_at = observed_at
                abnormal.append(finding)
                state_groups['abnormal'].append(state)
            else:
                state.status = AlertState.Status.NORMAL
                state.last_normal_at = observed_at
                if was_abnormal:
                    recovered.append(finding)
                    state_groups['recovery'].append(state)
            state.save(update_fields={
                'status', 'finding_snapshot', 'last_seen_at', 'last_abnormal_at', 'last_normal_at', 'updated_at',
            })

        routing = target.task.profile_snapshot.get('alert_routing')
        policy = (_policy_for_scope(scope[0], scope[1]) if routing is None else
                  AlertPolicy.objects.filter(pk=routing.get('effective_policy_id')).first())
        events = []
        for event_type, event_findings in (
            (AlertEvent.EventType.ABNORMAL, abnormal),
            (AlertEvent.EventType.RECOVERY, recovered),
        ):
            if not event_findings:
                continue
            defaults = _event_defaults(target, event_type, event_findings, observed_at, policy)
            event, created = AlertEvent.objects.get_or_create(
                target_run=target,
                event_type=event_type,
                defaults=defaults,
            )
            if not created:
                events.append(event)
                continue
            event.states.add(*state_groups['abnormal' if event_type == AlertEvent.EventType.ABNORMAL else 'recovery'])
            for state in state_groups['abnormal' if event_type == AlertEvent.EventType.ABNORMAL else 'recovery']:
                state.last_event = event
                state.save(update_fields={'last_event', 'updated_at'})
            event.status = 'recorded'
            event.save(update_fields={'status', 'updated_at'})
            events.append(event)
        return events


def findings_for_target(target_run):
    """Normalize explicit persisted results; never invent threshold findings."""
    target = TaskTargetRun.objects.select_related('task').get(pk=getattr(target_run, 'pk', target_run))
    snapshot = target.result_snapshot if isinstance(target.result_snapshot, dict) else {}
    if target.result_type == 'computer_analysis':
        from net.models import ComputerAnalysis

        analysis = ComputerAnalysis.objects.filter(pk=target.result_id).first()
        if analysis is None:
            return []
        exceptions = analysis.exceptions if isinstance(analysis.exceptions, list) else []
        findings = []
        failed_items = set()
        for issue in exceptions:
            if not isinstance(issue, dict):
                continue
            from net.devices.pc.severity import grade_issue
            issue = grade_issue(issue)
            item = str(issue.get('analysis_item') or '').strip()
            issue_type = str(issue.get('问题类型') or 'analysis exception').strip()
            key = f'analysis.{item}' if item else f'analysis.exception.{issue_type.casefold()[:120]}'
            if item:
                failed_items.add(item)
            findings.append(Finding(
                key=key,
                severity=issue['severity'],
                state='unknown' if issue['severity'] == 'info' else 'abnormal',
                title=issue_type,
                detail=str(issue.get('详细问题') or analysis.summary),
            ))
        for item in analysis.analysis_items if isinstance(analysis.analysis_items, list) else []:
            if item not in failed_items:
                findings.append(Finding(
                    key=f'analysis.{item}', severity='info', title=str(item),
                    detail='Selected analysis item completed normally.', state='normal',
                ))
        return findings
    if target.result_type.endswith('_inspection'):
        from net.inspections.result_storage import expanded_result_snapshot
        snapshot = expanded_result_snapshot(target)
        if snapshot is None:
            return []
        status = str(snapshot.get('status') or target.status)
        details = snapshot.get('details', {})
        if 'normal_issue_items' in details:
            from net.devices.pc.severity import grade_issue
            findings = []
            failed_items = set()
            def item_key(item):
                return 'inspection.collection' if item == 'inspection_collection' else f'inspection.{item}'
            for value in details.get('issue_findings', []):
                issue = grade_issue(value)
                item = issue['analysis_item']
                failed_items.add(item)
                findings.append(Finding(key=item_key(item), severity=issue['severity'], title=issue['问题类型'],
                    detail=str(issue.get('详细问题') or status), state='unknown' if issue['severity'] == 'info' else 'abnormal'))
            # Reachability alone is not a complete collection. Missing metric
            # evidence remains unknown, but a persisted failed/partial run must
            # not silently recover the independent collection alert. Preserve
            # explicitly graded collection findings (including policy overrides).
            if status != TaskRun.Status.SUCCESS and 'inspection_collection' not in failed_items:
                failed_items.add('inspection_collection')
                findings.append(Finding(
                    key='inspection.collection', severity='critical', title='Infrastructure collection',
                    detail=str(snapshot.get('summary') or target.error_message or status),
                ))
            for item in details['normal_issue_items']:
                if item not in failed_items:
                    findings.append(Finding(key=item_key(item), severity='info', title=item,
                                            detail='本次检查正常', state='normal'))
            return findings
        issues = snapshot.get('details', {}).get('issue_findings')
        if issues:
            from net.devices.pc.severity import grade_issue, RANK
            finding = max((grade_issue(issue) for issue in issues), key=lambda issue: RANK[issue['severity']])
            return [Finding(key='inspection.collection', severity=finding['severity'],
                            title=finding['问题类型'], detail=str(finding.get('详细问题') or status),
                            state='unknown' if finding['severity'] == 'info' else 'abnormal')]
        return [Finding(
            key='inspection.collection',
            severity='critical' if status != TaskRun.Status.SUCCESS else 'info',
            title='Infrastructure collection',
            detail=str(snapshot.get('summary') or target.error_message or status),
            state='normal' if status == TaskRun.Status.SUCCESS else 'abnormal',
        )]
    # A failed target is abnormal evidence, but it contains no normal evidence
    # for any previously selected finding and therefore cannot recover it.
    if target.status in {TaskRun.Status.FAILED, TaskRun.Status.PARTIAL}:
        return [Finding(
            key='execution.failure', severity='critical', title='Target execution failed',
            detail=target.error_message or target.status,
        )]
    return []


def _claim_deliveries(*, event=None, limit=1, now=None):
    now = now or timezone.now()
    event_id = getattr(event, 'pk', event)
    with transaction.atomic():
        due = Q(status=AlertDelivery.Status.PENDING)
        due |= Q(status=AlertDelivery.Status.RETRY, next_attempt_at__lte=now)
        due |= Q(status=AlertDelivery.Status.SENDING, lease_expires_at__lte=now)
        rows = AlertDelivery.objects.select_for_update().select_related('event', 'channel').filter(
            due, event__event_type='summary')
        if event_id is not None:
            rows = rows.filter(event_id=event_id)
        claimed = []
        for delivery in rows.order_by('created_at', 'pk')[:max(1, limit)]:
            if delivery.status == AlertDelivery.Status.SENDING and delivery.attempt_count >= delivery.max_attempts:
                delivery.status = AlertDelivery.Status.FAILED
                delivery.lease_expires_at = None
                delivery.lease_token = ''
                delivery.error_summary = 'Delivery lease expired after the final attempt.'
                delivery.save(update_fields={'status', 'lease_expires_at', 'lease_token', 'error_summary', 'updated_at'})
                _event_status(delivery.event_id)
                continue
            delivery.status = AlertDelivery.Status.SENDING
            delivery.attempt_count += 1
            delivery.attempted_at = now
            delivery.lease_expires_at = now + timedelta(seconds=DELIVERY_LEASE_SECONDS)
            delivery.lease_token = uuid4().hex
            delivery.next_attempt_at = None
            delivery.save(update_fields={
                'status', 'attempt_count', 'attempted_at', 'lease_expires_at', 'lease_token',
                'next_attempt_at', 'updated_at',
            })
            claimed.append((delivery.pk, delivery.lease_token))
        return claimed


def _event_status(event_id):
    event = AlertEvent.objects.select_for_update().get(pk=event_id)
    statuses = list(event.deliveries.values_list('status', flat=True))
    if statuses and all(status == AlertDelivery.Status.SENT for status in statuses):
        event.status = AlertEvent.Status.DELIVERED
    elif AlertDelivery.Status.SENT in statuses:
        event.status = AlertEvent.Status.PARTIAL
    elif statuses and all(status == AlertDelivery.Status.FAILED for status in statuses):
        event.status = AlertEvent.Status.FAILED
    elif AlertDelivery.Status.SENDING in statuses:
        event.status = AlertEvent.Status.SENDING
    else:
        event.status = AlertEvent.Status.PENDING
    event.save(update_fields={'status', 'updated_at'})


def _finish_delivery(delivery_id, token, result):
    now = timezone.now()
    with transaction.atomic():
        delivery = AlertDelivery.objects.select_for_update().select_related('event').get(pk=delivery_id)
        if delivery.status != AlertDelivery.Status.SENDING or delivery.lease_token != token:
            return delivery
        delivery.response_summary = summarize(result.response_summary, *delivery.channel.settings.values())
        delivery.error_summary = '' if result.success else delivery.response_summary
        delivery.lease_expires_at = None
        delivery.lease_token = ''
        if result.success:
            delivery.status = AlertDelivery.Status.SENT
            delivery.delivered_at = now
            delivery.next_attempt_at = None
        elif result.retryable and delivery.attempt_count < delivery.max_attempts:
            delivery.status = AlertDelivery.Status.RETRY
            delivery.next_attempt_at = now + timedelta(seconds=RETRY_DELAY_SECONDS)
            delivery.delivered_at = None
        else:
            delivery.status = AlertDelivery.Status.FAILED
            delivery.next_attempt_at = None
            delivery.delivered_at = None
        delivery.save(update_fields={
            'status', 'delivered_at', 'next_attempt_at', 'lease_expires_at', 'lease_token',
            'response_summary', 'error_summary', 'updated_at',
        })
        _event_status(delivery.event_id)
        return delivery


def deliver_due_alerts(*, event=None, limit=1):
    """Claim due rows briefly, send outside locks, then fence the result."""
    delivered = []
    for delivery_id, token in _claim_deliveries(event=event, limit=limit):
        delivery = AlertDelivery.objects.select_related('event', 'channel').get(pk=delivery_id)
        try:
            result = send_alert(delivery.channel, build_alert_message(delivery.event))
        except Exception as exc:  # The durable row records an unexpected adapter failure.
            from net.alerts.base import DeliveryResult

            result = DeliveryResult(
                False, summarize(str(exc), *delivery.channel.settings.values()), True,
            )
        delivered.append(_finish_delivery(delivery_id, token, result))
    return delivered


def deliver_event(event) -> list[AlertDelivery]:
    return deliver_due_alerts(event=event, limit=100)


def process_persisted_target(target_run):
    """Commit alert rows and completion together, independently of the record.

    Failed processing stays eligible. Its attempt time moves it behind untouched
    work and older retries, so poison input cannot pin the reconciliation batch.
    """
    from net.inspections.executor import _database_guard

    target_id = getattr(target_run, 'pk', target_run)
    with _database_guard():
        try:
            with transaction.atomic():
                target = TaskTargetRun.objects.select_for_update().get(pk=target_id)
                if target.task.task_type not in ('inspection', 'computer_analysis'):
                    return []
                if target.status not in TaskRun.TERMINAL_STATUSES or target.alert_processed_at is not None:
                    return []
                findings = findings_for_target(target)
                events = process_target_findings(target, findings)
                observation = _normalised_findings(findings)
                snapshot = dict(target.result_snapshot or {})
                snapshot['alert_observation'] = {
                    'abnormal': any(item['state'] == 'abnormal' for item in observation),
                    'info': any(item['state'] == 'unknown' for item in observation),
                }
                now = timezone.now()
                TaskTargetRun.objects.filter(pk=target.pk).update(
                    alert_processed_at=now, alert_attempted_at=now, alert_processing_error='',
                    result_snapshot=snapshot,
                )
                return events
        except Exception as exc:
            # The failed transaction rolled back all alert/state writes. Audit
            # separately without reserving an event's unique slot with a fake
            # finding, which would otherwise prevent a later successful retry.
            TaskTargetRun.objects.filter(pk=target_id, alert_processed_at__isnull=True).update(
                alert_attempted_at=timezone.now(),
                alert_processing_error=f'Alert processing failed ({type(exc).__name__}).',
            )
            return []


def reconcile_terminal_targets(*, limit=100):
    """Replay terminal results after a restart between record commit and alert work."""
    targets = list(TaskTargetRun.objects.filter(
        status__in=TaskRun.TERMINAL_STATUSES,
        alert_processed_at__isnull=True,
        task__task_type__in=('inspection', 'computer_analysis'),
    ).order_by(
        F('alert_attempted_at').asc(nulls_first=True), 'finished_at', 'pk')[:limit])
    for target in targets:
        process_persisted_target(target)
