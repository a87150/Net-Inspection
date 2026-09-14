"""Test fixtures for immutable remote evidence; never a production scanner adapter."""
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from uuid import uuid4

from django.utils import timezone

from net.devices.pc.logs import import_log_bytes
from net.models import Computer, ComputerLogFile


def import_payload(payload, *, source_path='incoming/test.json', modified_at=None):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    return import_log_bytes(
        raw=raw, source_path=source_path, remote_source_path=source_path,
        modified_at=modified_at or timezone.now(), source_protocol='smb', transfer=None,
    )


def import_fixture_path(path):
    return import_payload(path.read_bytes(), source_path=str(path),
                          modified_at=datetime.fromtimestamp(path.stat().st_mtime, dt_timezone.utc)).log_file


def create_log_file(**fields):
    """Supply mandatory identity for synthetic queue/UI records, not importer tests."""
    if fields.get('import_status') == 'imported':
        if not fields.get('computer') and not fields.get('computer_id'):
            system = (fields.get('payload') or {}).get('系统信息概览') or {}
            name = system.get('计算机名') or f'TEST-LOG-{uuid4().hex[:16]}'
            fields['computer'], _ = Computer.objects.get_or_create(computer_name=name)
        if not fields.get('collected_date'):
            computer_id = fields.get('computer_id') or fields['computer'].pk
            day = timezone.localdate(fields.get('modified_at') or timezone.now())
            while ComputerLogFile.objects.filter(computer_id=computer_id, collected_date=day,
                                                  import_status='imported').exists():
                day -= timedelta(days=1)
            fields['collected_date'] = day
    return ComputerLogFile.objects.create(**fields)


def analysis_task_url(*records):
    """Bind legacy result fixtures to an explicit task for detail-table tests."""
    from django.urls import reverse
    from net.models import ComputerAnalysis, ComputerAnalysisProfile, TaskRun, TaskTargetRun
    profile = ComputerAnalysisProfile.objects.create(name=f'Detail-{uuid4().hex}')
    task = TaskRun.objects.create(task_type='computer_analysis', source='manual', analysis_profile=profile)
    for record in records or ComputerAnalysis.objects.all():
        target, _ = TaskTargetRun.objects.get_or_create(task=task, target_type='computer_log',
            target_id=str(record.log_file_id), defaults={'result_type': 'computer_analysis', 'result_id': str(record.pk)})
        record.task_target = target
        record.save(update_fields=['task_target'])
    return reverse('task_detail', args=[task.pk])
