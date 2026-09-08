from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .forms import DEVICE_KINDS, device_form


@login_required
@require_POST
def asset_create(request, kind):
    if kind not in DEVICE_KINDS:
        raise Http404('该项目不支持手动添加设备。')
    form = device_form(kind, request.POST)
    if form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(None, '设备保存冲突，请检查 IP 是否已存在。')
        else:
            messages.success(request, '设备已添加，可在列表中查看；尚未执行巡检。')
            return redirect('asset_list', kind=kind)
    from .views import asset_list
    return asset_list(request, kind, creation_form=form)
