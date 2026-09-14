"""Administrator-only, uncached, single-computer recovery-key lookup."""
import logging

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_POST

from index.common.access import admin_required
from net.domain.bitlocker import read_bitlocker_keys
from net.models import Domain_Computer, Domain_Controller_Config

logger = logging.getLogger(__name__)


@never_cache
@admin_required
@require_POST
@sensitive_variables()
def domain_computer_bitlocker(request, pk):
    computer = get_object_or_404(Domain_Computer, pk=pk)
    config = Domain_Controller_Config.objects.filter(pk=1).first()
    if config is None:
        return JsonResponse({"message": "请先配置域控连接。"}, status=400)
    try:
        records = read_bitlocker_keys(config, computer)
    except ValidationError as exc:
        logger.warning("BitLocker lookup failed user=%s computer=%s", request.user.pk, computer.pk)
        return JsonResponse({"message": "；".join(exc.messages)}, status=400)
    logger.warning("BitLocker lookup user=%s computer=%s records=%s", request.user.pk, computer.pk, len(records))
    message = "请按恢复界面上的密钥 ID 匹配记录。" if records else "未找到可见的 AD 恢复记录：可能未备份到此域，或当前绑定账号无权查看。不能据此判断设备未启用 BitLocker。"
    response = JsonResponse({"records": records, "message": message})
    response["Referrer-Policy"] = "no-referrer"
    return response
