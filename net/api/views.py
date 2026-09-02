"""Public upload receipts; analysis is exclusively Worker-owned."""
import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from net.models import ComputerAnalysisProfile, ComputerLogFile
from net.devices.pc.logs import _refresh_static_computer, _computer_defaults, _isolated_retry
from net.infrastructure.sanitization import sanitize
from net.inspections.queue import enqueue_task


class ComputerInspectionView(APIView):
    def post(self, request):
        data = request.data
        if not isinstance(data, dict):
            return Response({'detail': '请求体必须是 JSON 对象。'}, status=400)
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        digest = hashlib.sha256(raw).hexdigest()
        payload = sanitize(data)
        def receive():
            with transaction.atomic():
                log = ComputerLogFile.objects.select_for_update().filter(content_hash=digest).first()
                created = log is None
                if log is not None and log.upload_task_id:
                    return Response({'created': False, 'log_id': log.pk,
                                     'task_id': str(log.upload_task_id) if log.upload_task_id else None,
                                     'message': '日志已接收；分析结果请查看任务。'}, status=200)
                if log is not None and log.import_status != 'imported':
                    raise ValidationError('该日志未成功导入，无法排队分析。')
                profile_id = request.query_params.get('profile_id') or getattr(settings, 'COMPUTER_UPLOAD_PROFILE_ID', '')
                profiles = ComputerAnalysisProfile.objects.filter(is_enabled=True)
                if profile_id:
                    profile = profiles.filter(pk=profile_id).first()
                    if profile is None:
                        raise ValidationError('上传分析配置不存在或已停用。')
                else:
                    profile = profiles.order_by('created_at', 'pk').first()
                    if profile is None:
                        profile = ComputerAnalysisProfile.objects.create(
                            name='PowerShell 上传默认分析', analysis_items=['activation', 'bitlocker', 'defender', 'patches'])
                if created:
                    _refresh_static_computer(payload, timezone.now())
                    _, collected_at, _ = _computer_defaults(payload, timezone.now())
                    log = ComputerLogFile.objects.create(content_hash=digest, source_path=f'api://upload/{digest}.json',
                        modified_at=collected_at, file_size=len(raw), import_status='imported', payload=payload)
                task = enqueue_task(profile, [log.pk], 'manual', overrides={'parameters': {'ingestion': 'upload'}})
                log.upload_task = task
                log.save(update_fields=['upload_task'])
                return Response({'created': created, 'log_id': log.pk, 'task_id': str(task.pk),
                                 'message': '日志已接收，分析已排队。'}, status=202 if created else 200)
        try:
            return _isolated_retry(receive)
        except (ValidationError, ValueError, TypeError) as exc:
            return Response({'detail': sanitize(str(exc))}, status=400)
