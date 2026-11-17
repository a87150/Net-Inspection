"""Administrator-only task-summary template editor and local preview."""
from django import forms
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from index.common.access import admin_required
from net.alerts.templates import (
    MAX_BODY_TEMPLATE_LENGTH, MODE_CHOICES, TEMPLATE_KEY, VARIABLES,
    get_task_summary_template, render_task_summary, validate_template,
)


class AlertNotificationTemplateForm(forms.Form):
    mode = forms.ChoiceField(label='展示模式', choices=MODE_CHOICES,
                            widget=forms.Select(attrs={'class': 'form-select'}))
    title_template = forms.CharField(label='自定义标题', max_length=255, required=False,
        validators=[validate_template], widget=forms.TextInput(attrs={'class': 'form-control'}))
    body_template = forms.CharField(label='自定义正文', max_length=MAX_BODY_TEMPLATE_LENGTH,
        required=False, validators=[validate_template],
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 6}))


PREVIEW_DATA = {
    'task_name': '示例服务器巡检', 'task_status': '部分成功', 'task_type': '服务器巡检',
    'total': 10, 'normal': 7, 'abnormal': 2, 'recovered': 1, 'failed': 1,
    'cancelled': 0, 'skipped': 0, 'info': 0,
    'started_at': '2026-09-09 09:00:00', 'finished_at': '2026-09-09 09:01:30',
    'duration': '90 秒', 'details_url': '/tasks/00000000-0000-0000-0000-000000000001/',
    'issues': ['示例服务器 A：磁盘使用率偏高', '示例服务器 B：采集失败'],
    'recoveries': ['示例服务器 C：CPU 使用率恢复正常'],
}


@never_cache
@admin_required
@require_http_methods(['GET', 'POST'])
@csrf_protect
def alert_template_settings(request):
    template = get_task_summary_template()
    initial = {key: getattr(template, key, '') for key in ('mode', 'title_template', 'body_template')}
    initial['mode'] = initial['mode'] or 'compact'
    form = AlertNotificationTemplateForm(request.POST if request.method == 'POST' else None,
                                         initial=initial, auto_id='alert-template-%s')
    saved, preview, status = False, None, 200
    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        valid = form.is_valid()
        if action not in ('save', 'preview'):
            form.add_error(None, '未知操作，请选择保存或预览。')
            valid = False
        if valid:
            if action == 'save':
                from net.models import AlertNotificationTemplate
                AlertNotificationTemplate.objects.update_or_create(key=TEMPLATE_KEY, defaults=form.cleaned_data)
                saved = True
            preview = render_task_summary(PREVIEW_DATA, template=form.cleaned_data)
        else:
            status = 400
    return render(request, 'alerts/template_modal.html', {
        'template_form': form, 'template_saved': saved, 'template_preview': preview,
        'template_variables': VARIABLES,
    }, status=status)
