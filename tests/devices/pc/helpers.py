"""Factories for API-uploaded PC log fixtures and synthetic analysis records."""
import json
from datetime import datetime, timezone as dt_timezone
from uuid import uuid4

from django.utils import timezone

from net.devices.pc.ingestion import ingest_log
from net.models import Computer, ComputerLogFile, PCUploadConfig


def _upload_config():
    config, _ = PCUploadConfig.objects.get_or_create(
        pk=1, defaults={'endpoint_url': 'http://testserver/api/pc/logs/'})
    if not config.is_enabled:
        config.is_enabled = True
        config.save(update_fields=['is_enabled'])
    return config


def import_payload(payload, *, source_path=None, modified_at=None):
    """Create API-era log evidence for analysis tests without remote protocols."""
    if isinstance(payload, bytes):
        payload = json.loads(payload.decode('utf-8-sig'))
    else:
        payload = dict(payload)
    payload.setdefault('platform', 'windows')
    if modified_at is not None and not payload.get('日志时间'):
        stamp = timezone.localtime(modified_at)
        payload['日志时间'] = stamp.strftime('%Y-%m-%d %H:%M:%S')
    return ingest_log(json.dumps(payload, ensure_ascii=False).encode('utf-8'), require_enabled=False)


def import_fixture_path(path):
    return import_payload(path.read_bytes(), modified_at=datetime.fromtimestamp(
        path.stat().st_mtime, dt_timezone.utc)).log_file


def create_log_file(**fields):
    """Supply mandatory identity for synthetic queue/UI records, not ingestion tests."""
    fields.pop('import_status', None)
    stamp = fields.pop('modified_at', None) or fields.get('collected_at') or timezone.now()
    if isinstance(stamp, str):
        from django.utils.dateparse import parse_datetime
        stamp = parse_datetime(stamp)
    fields.pop('source_path', None)
    fields.pop('source_protocol', None)
    fields.pop('remote_source_path', None)
    fields.pop('archived_path', None)
    fields.pop('parse_error', None)
    payload = fields.get('payload') or {}
    if not fields.get('computer') and not fields.get('computer_id'):
        system = payload.get('系统信息概览') or {}
        name = system.get('计算机名') or f'TEST-LOG-{uuid4().hex[:16]}'
        fields['computer'], _ = Computer.objects.get_or_create(computer_name=name)
    fields.setdefault('collected_at', stamp)
    fields.setdefault('collected_date', timezone.localdate(stamp))
    fields.setdefault('platform', payload.get('platform') or 'windows')
    fields.setdefault('content_hash', uuid4().hex + uuid4().hex)
    return ComputerLogFile.objects.create(**fields)


def analysis_task_url(*records):
    """Bind result fixtures to an explicit task for detail-table tests."""
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
