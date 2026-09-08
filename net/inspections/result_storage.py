"""Small task result references; immutable records own the complete evidence."""


def compact_result_snapshot(record, *, result_type, health_status=None):
    details = record.details if isinstance(record.details, dict) else {}
    if health_status is None:
        health_status = details.get('health_status')
    if health_status not in {'normal', 'abnormal'}:
        health_status = 'abnormal' if record.status != 'success' or any(
            issue.get('severity') != 'info' for issue in details.get('issue_findings', [])
            if isinstance(issue, dict)) else 'normal'
    value = {'snapshot_version': 2, 'result_type': result_type, 'result_id': str(record.pk),
             'status': record.status, 'health_status': health_status, 'summary': record.summary}
    for key in ('is_reachable', 'duration_ms'):
        if hasattr(record, key):
            value[key] = getattr(record, key)
    return value


def expanded_result_snapshot(target):
    """Resolve v2 references only when evidence is needed; old snapshots still work.

    A missing referenced record is unknown evidence, never a normal observation.
    """
    snapshot = target.result_snapshot if isinstance(target.result_snapshot, dict) else {}
    if snapshot.get('snapshot_version') != 2:
        return snapshot
    from net.models import ComputerAnalysis, Network_Device_Inspection, Server_Inspection, Monitor_Inspection
    model = {'computer_analysis': ComputerAnalysis, 'network_device_inspection': Network_Device_Inspection,
             'server_inspection': Server_Inspection, 'monitor_inspection': Monitor_Inspection}.get(target.result_type)
    if model is None:
        return None
    fields = ('details', 'exceptions', 'analysis_items') if target.result_type == 'computer_analysis' else ('details',)
    record = model.objects.filter(pk=target.result_id, task_target_id=target.pk).only(*fields).first()
    return {**snapshot, **{field: getattr(record, field) for field in fields}} if record is not None else None
