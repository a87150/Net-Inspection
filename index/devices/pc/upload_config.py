"""Administrator controls for the terminal write-only API."""
import secrets

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from net.models import PCUploadConfig


class PCUploadConfigForm(forms.ModelForm):
    token = forms.CharField(required=False, label='采集令牌',
        help_text='首次保存会自动生成。留空会保留已保存的令牌。',
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}))

    class Meta:
        model = PCUploadConfig
        fields = ('endpoint_url', 'is_enabled', 'log_retention')
        labels = {'endpoint_url': '终端上报地址', 'is_enabled': '启用终端 API 上报', 'log_retention': '全局日志保留策略'}
        help_texts = {'endpoint_url': '填写终端可访问的完整 HTTP(S) 地址。', 'log_retention': '每天仅保留每台 PC 最新日志，或保留全部版本。'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = ('form-check-input' if isinstance(field.widget, forms.CheckboxInput)
                                            else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    def save(self, commit=True):
        config = super().save(commit=False)
        config.pk = 1
        token = self.cleaned_data['token']
        if token:
            config.set_token(token)
        elif config._state.adding:
            config.set_token(secrets.token_urlsafe(32))
        config.full_clean()
        if commit:
            config.save()
        return config


def _config_redirect():
    return redirect(f'{reverse("computer_analysis_list")}?task_modal=profile')


@require_POST
def pc_upload_config_save(request):
    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied
    form = PCUploadConfigForm(request.POST, instance=PCUploadConfig.load())
    try:
        if not form.is_valid():
            raise ValidationError('；'.join(message for errors in form.errors.values() for message in errors))
        form.save()
    except ValidationError as exc:
        messages.error(request, '采集 API 配置未保存：' + '；'.join(exc.messages))
        return _config_redirect()
    messages.success(request, 'PC 采集 API 配置已保存。')
    return _config_redirect()


@require_POST
def pc_upload_config_reset_token(request):
    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied
    config = PCUploadConfig.load()
    if config is None:
        messages.error(request, '请先保存 PC 采集 API 配置。')
        return _config_redirect()
    config.set_token(secrets.token_urlsafe(32))
    config.save(update_fields=('encrypted_token', 'token_hash', 'updated_at'))
    messages.success(request, '采集令牌已重置，请重新下载采集包并部署。')
    return _config_redirect()
