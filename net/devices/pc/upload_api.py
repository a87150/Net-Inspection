"""Write-only terminal endpoint. Bearer tokens never grant UI/read access."""
from django.core.exceptions import ValidationError, RequestDataTooBig
from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from net.models import PCUploadConfig
from .ingestion import ingest_log
from .logs import MAX_LOG_FILE_BYTES

@csrf_exempt
@require_POST
def pc_log_upload(request):
    config = PCUploadConfig.load()
    authorization = request.headers.get('Authorization', '')
    token = authorization[7:] if authorization.startswith('Bearer ') else ''
    if not config or not config.accepts_token(token):
        return JsonResponse({'error': '采集令牌无效或上报已停用。'}, status=401)
    if request.content_type != 'application/json':
        return JsonResponse({'error': '请使用 application/json。'}, status=415)
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
        if length > MAX_LOG_FILE_BYTES:
            raise RequestDataTooBig()
        # Read at most the limit even if the client omits Content-Length.
        raw = request.read(MAX_LOG_FILE_BYTES + 1)
        if len(raw) > MAX_LOG_FILE_BYTES:
            raise RequestDataTooBig()
        outcome = ingest_log(raw)
    except RequestDataTooBig:
        return JsonResponse({'error': '日志超过 16 MB。'}, status=413)
    except ValidationError as exc:
        return JsonResponse({'error': '；'.join(exc.messages)}, status=400)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return JsonResponse({'error': '日志字段格式无效。'}, status=400)
    except DatabaseError:
        return JsonResponse({'error': '日志暂时无法保存，请稍后重试。'}, status=503)
    return JsonResponse({'status': outcome.status, 'log_id': outcome.log_file.pk},
                        status=201 if outcome.status == 'created' else 200)
