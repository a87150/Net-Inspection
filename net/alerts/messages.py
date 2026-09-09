"""Normalize persisted alert events into transport-neutral messages."""

from net.infrastructure.sanitization import sanitize

from .base import AlertMessage


def _display(value):
    # Scrub structured facts before string conversion preserves only their repr.
    return str(sanitize(value))


def _finding_text(finding):
    title = _display(finding.get('title') or finding.get('key') or 'Unknown finding')
    severity = _display(finding.get('severity') or 'unknown')
    detail = _display(finding.get('detail') or '')
    return f'[{severity}] {title}' + (f': {detail}' if detail else '')


def build_alert_message(event):
    """Build one message containing the event's complete run-target context."""
    event_type = _display(event.event_type)
    if event_type == 'summary':
        from .templates import render_task_summary
        data = dict(getattr(event, 'summary_data', None) or {})
        detail_url = _display(data.get('details_url') or f'/tasks/{event.task_id}/')
        data['details_url'] = detail_url
        title, text = data.get('message_title'), data.get('message_text')
        if not isinstance(title, str) or not isinstance(text, str):
            # Legacy summaries must never pick up edits made after the event.
            defaults = render_task_summary(data, template={})
            title = title if isinstance(title, str) else defaults['title']
            text = text if isinstance(text, str) else defaults['text']
        return AlertMessage(title=title, text=text,
                            facts={'event_type': 'summary'}, detail_url=detail_url)
    findings = list(event.findings or [])
    finding_titles = [_display(item.get('title') or item.get('key') or 'Unknown finding') for item in findings]
    project = f'{_display(event.profile_type)}/{_display(event.profile_id)}'
    target = f'{_display(event.target_type)}/{_display(event.target_id)}'
    run_time = _display(event.occurred_at.isoformat() if hasattr(event.occurred_at, 'isoformat') else event.occurred_at)
    detail_url = f'/alerts/{_display(event.id)}/'

    if event_type == 'recovery':
        title = 'Recovery alert' + (f': {", ".join(finding_titles)}' if finding_titles else '')
    else:
        title = 'Abnormal alert' + (f': {", ".join(finding_titles)}' if finding_titles else '')

    lines = [
        f'Event: {event_type}',
        f'Project: {project}',
        f'Target: {target}',
        f'Run time: {run_time}',
    ]
    if event_type == 'recovery':
        lines.append('Recovered finding(s): ' + (', '.join(finding_titles) or 'none recorded'))
    lines.append('Merged findings:')
    lines.extend(f'- {_finding_text(finding)}' for finding in findings)
    if event.summary:
        lines.append(f'Summary: {_display(event.summary)}')
    lines.append(f'Details: {detail_url}')
    return AlertMessage(
        title=title,
        text='\n'.join(lines),
        facts={
            'event_type': event_type,
            'project': project,
            'target': target,
            'run_time': run_time,
            'findings': ', '.join(finding_titles),
        },
        detail_url=detail_url,
    )
