"""Select stored evidence by the same source/path/date scope as remote discovery."""
from datetime import datetime, time, timedelta
import re

from django.db.models import Q
from django.utils import timezone

from net.models import ComputerLogTransfer
from .configuration import ORIGIN_FIELDS
from .connectors.base import file_date_range, normalize_path


def stored_log_ids(source, *, now):
    start, end = file_date_range(source, now=now)
    zone = timezone.get_current_timezone()
    incoming = normalize_path(source.remote_incoming_directory)
    prefix = incoming + '/' if incoming else ''
    # Compare UTC timestamps instead of __date, which needs MySQL timezone tables.
    transfers = ComputerLogTransfer.objects.filter(
        source_id=source.pk, log_file__import_status='imported',
        observed_mtime__gte=datetime.combine(start, time.min, tzinfo=zone),
        observed_mtime__lt=datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone),
        remote_source_path__startswith=prefix, remote_source_path__iendswith='.json',
        **{'source_snapshot__' + key: getattr(source, key) for key in ORIGIN_FIELDS},
    )
    for directory in (source.remote_processed_directory, source.remote_failed_directory):
        directory = normalize_path(directory)
        if directory:
            transfers = transfers.exclude(Q(remote_source_path=directory) |
                                          Q(remote_source_path__startswith=directory + '/'))
    if not source.recursive:
        transfers = transfers.filter(remote_source_path__regex='^' + re.escape(prefix) + r'[^/]+\.[jJ][sS][oO][nN]$')
    return list(transfers.order_by('log_file_id').values_list('log_file_id', flat=True).distinct())
