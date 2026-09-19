"""Database-selected latest evidence for both scheduled and manual analysis."""
from django.db.models import OuterRef, Subquery
from net.models import ComputerLogFile

def latest_logs(computer_ids=None):
    rows = ComputerLogFile.objects.filter(retained=True)
    latest = rows.filter(computer_id=OuterRef('computer_id')).order_by('-collected_at', '-pk').values('pk')[:1]
    if computer_ids is not None:
        rows = rows.filter(computer_id__in=computer_ids)
    return rows.filter(pk=Subquery(latest))

def enqueue_latest_analysis(profile, source, overrides=None, computer_ids=None):
    from net.inspections.queue import enqueue_task
    return enqueue_task(profile, latest_logs(computer_ids).values_list('pk', flat=True), source, overrides)
