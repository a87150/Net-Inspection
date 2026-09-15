"""Secret-safe alert configuration, history, and operator test-send views."""

from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from django.contrib import messages
from index.common.access import is_admin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from index.alerts.forms import DingTalkAlertChannelForm, EmailAlertChannelForm, FeishuAlertChannelForm
from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition
from net.alerts import AlertMessage, send_alert
from net.alerts.base import summarize
from net.models import (
    AlertChannel, AlertEvent, AlertPolicy, AlertTestSend,
    ComputerAnalysisProfile, InspectionProfile,
)


CHANNEL_FORMS = {
    AlertChannel.ChannelType.FEISHU: FeishuAlertChannelForm,
    AlertChannel.ChannelType.DINGTALK: DingTalkAlertChannelForm,
    AlertChannel.ChannelType.EMAIL: EmailAlertChannelForm,
}


def _safe_next(request, value, fallback):
    candidate = (value or '').strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        parsed = urlsplit(candidate)
        if parsed.path.startswith('/') and not parsed.path.startswith('//'):
            return candidate
    return fallback


def _channel_form(channel_type, *, instance=None, data=None):
    form_class = CHANNEL_FORMS.get(channel_type, FeishuAlertChannelForm)
    return form_class(data, instance=instance)


def _policy_for_profile(profile):
    if isinstance(profile, InspectionProfile):
        return AlertPolicy.objects.filter(inspection_profile=profile).first()
    if isinstance(profile, ComputerAnalysisProfile):
        return AlertPolicy.objects.filter(analysis_profile=profile).first()
    return None


def _failure_redirect(request, next_url, modal, *, state, **query):
    """Carry only explicitly selected non-secret state across one redirect.

    Discard arbitrary return-query values, including credential-like parameters.
    The token prevents another tab's modal from consuming this form state.
    """
    path = urlsplit(next_url).path
    allowed_paths = {reverse('alert_list')}
    if modal == 'policy':
        allowed_paths.add(reverse('computer_analysis_list'))
        allowed_paths.update(reverse('asset_list', args=[kind])
                             for kind in ('computers', 'networks', 'servers', 'monitors'))
        allowed_paths.update(reverse('record_list', args=[kind])
                             for kind in ('networks', 'servers', 'monitors'))
    if path not in allowed_paths:
        path = reverse('alert_list')
    token = uuid4().hex
    query = {'alert_modal': modal, **query, 'alert_form_token': token}
    request.session['alert_form_failure'] = {'token': token, 'modal': modal, **state}
    return redirect(f'{path}?{urlencode(query)}')


def alert_modal_context(request, *, profile=None):
    """Context shared by alert-history and task-profile pages.

    It intentionally exposes only channel labels and non-secret form values.
    """
    if not is_admin(request.user):
        return {}
    failure = request.session.get('alert_form_failure', {})
    if (failure.get('token') == request.GET.get('alert_form_token')
            and failure.get('modal') == request.GET.get('alert_modal')):
        request.session.pop('alert_form_failure', None)
    else:
        failure = {}
    if request.GET.get('alert_modal') == 'policy':
        requested_scope = request.GET.get('alert_policy_scope')
        if requested_scope == 'default':
            profile = None
        elif requested_scope in {'inspection', 'analysis'}:
            model = InspectionProfile if requested_scope == 'inspection' else ComputerAnalysisProfile
            try:
                profile = get_object_or_404(model, pk=request.GET.get('alert_policy_profile_id'))
            except (ValidationError, ValueError):
                raise Http404('未知的告警策略配置') from None
    channel_id = request.GET.get('alert_channel_id', '').strip()
    channel = None
    if channel_id:
        try:
            channel = AlertChannel.objects.filter(pk=channel_id).first()
        except (ValidationError, ValueError):
            channel = None
    channel_type = request.GET.get('alert_channel_type', '')
    if channel is not None:
        channel_type = channel.channel_type
    if channel_type not in CHANNEL_FORMS:
        channel_type = AlertChannel.ChannelType.FEISHU

    default_policy = AlertPolicy.objects.filter(default_slot=AlertPolicy.DEFAULT_SLOT).first()
    policy = _policy_for_profile(profile) if profile is not None else default_policy
    scope = 'default'
    profile_id = ''
    if isinstance(profile, InspectionProfile):
        scope, profile_id = 'inspection', str(profile.pk)
    elif isinstance(profile, ComputerAnalysisProfile):
        scope, profile_id = 'analysis', str(profile.pk)
    selected_channels = set(policy.channels.values_list('pk', flat=True)) if policy else set()
    mode = policy.mode if policy else (
        AlertPolicy.Mode.OVERRIDE if scope == 'default' else AlertPolicy.Mode.INHERIT
    )
    effective_source = default_policy if mode == AlertPolicy.Mode.INHERIT else policy
    channel_form = _channel_form(channel_type, instance=channel)
    if failure.get('modal') == 'channel':
        channel_form.initial.update(failure['values'])
    context = {
        'alert_channels': AlertChannel.objects.order_by('name', 'pk'),
        'alert_channel_form': channel_form,
        'alert_channel_errors': failure.get('errors', []) if failure.get('modal') == 'channel' else [],
        'alert_channel_type': channel_type,
        'alert_edit_channel': channel,
        'alert_policy': policy,
        'alert_policy_name': policy.name if policy else '默认告警策略',
        'alert_policy_errors': failure.get('errors', []) if failure.get('modal') == 'policy' else [],
        'alert_policy_mode': mode,
        'alert_policy_scope': scope,
        'alert_policy_profile_id': profile_id,
        'alert_policy_channel_ids': selected_channels,
        'alert_inherited_channels': list(default_policy.channels.order_by('name', 'pk')) if default_policy else [],
        'alert_default_source_name': default_policy.name if default_policy else '尚未配置默认策略',
        'alert_effective_source_name': (
            effective_source.name if effective_source is not None else '尚未配置默认策略'
        ),
        'alert_channel_modal_auto_open': request.GET.get('alert_modal') == 'channel',
        'alert_policy_modal_auto_open': request.GET.get('alert_modal') == 'policy',
    }
    if failure.get('modal') == 'policy':
        context.update({
            'alert_policy_name': failure['name'],
            'alert_policy_mode': failure['mode'],
            'alert_policy_channel_ids': {UUID(pk) for pk in failure['channels']},
        })
    return context


def _alert_events():
    return AlertEvent.objects.select_related('task', 'target_run', 'policy').prefetch_related(
        'deliveries__channel',
    )


def alert_list(request):
    definition = get_table_definition('alert_events')
    events, table_state = apply_table_filters(
        request, _alert_events(), definition, include_legacy_status=False,
    )
    page_obj = Paginator(events, table_state['page_size']).get_page(request.GET.get('page'))
    context = {
        'page_obj': page_obj,
        'table_definition': definition,
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=['alert_events']),
        'pagination_query': query_without_page(request),
    }
    context.update(alert_modal_context(request))
    return render(request, 'alerts/list.html', context)


def alert_detail(request, pk):
    event = get_object_or_404(_alert_events(), pk=pk)
    return render(request, 'alerts/detail.html', {'event': event})


@require_POST
def alert_channel_save(request):
    next_url = _safe_next(request, request.POST.get('next'), reverse('alert_list'))
    channel_type = request.POST.get('channel_type', '')
    channel = None
    channel_id = request.POST.get('channel_id', '').strip()
    if channel_id:
        channel = get_object_or_404(AlertChannel, pk=channel_id)
        channel_type = channel.channel_type
    if channel_type not in CHANNEL_FORMS:
        messages.error(request, '渠道未保存：请选择支持的告警渠道类型。')
        return _failure_redirect(request, next_url, 'channel',
                                 state={'values': {}, 'errors': ['请选择支持的渠道类型。']},
                                 alert_channel_type='feishu')
    form = _channel_form(channel_type, instance=channel, data=request.POST)
    valid = form.is_valid()
    try:
        saved = form.save() if valid else None
    except ValidationError:
        form.add_error(None, '渠道配置无效，请检查地址、邮箱或 TLS 设置。')
        saved = None
    if saved is None:
        messages.error(request, '渠道未保存：渠道配置无效，请检查必填项。')
        safe_fields = {'name', 'is_enabled', 'smtp_host', 'smtp_port', 'use_tls',
                       'use_ssl', 'username', 'from_email', 'recipients'}
        values = {name: form[name].value() for name in form.fields if name in safe_fields}
        errors = [f'{form.fields[name].label if name in form.fields else "配置"}: {error}'
                  for name, items in form.errors.items() for error in items]
        return _failure_redirect(
            request, next_url, 'channel', state={'values': values, 'errors': errors},
            alert_channel_type=channel_type, alert_channel_id=str(channel.pk) if channel else '',
        )
    messages.success(request, f'已保存告警渠道“{saved.name}”。')
    return _failure_redirect(request, next_url, 'channel', state={'values': {}, 'errors': []},
                             alert_channel_type=saved.channel_type, alert_channel_id=str(saved.pk))


def _policy_scope_from_post(request):
    scope = request.POST.get('scope', '')
    profile_id = request.POST.get('profile_id', '').strip()
    if scope == 'default':
        return scope, None
    model = InspectionProfile if scope == 'inspection' else ComputerAnalysisProfile if scope == 'analysis' else None
    if model is None:
        raise Http404('未知的告警策略范围')
    return scope, get_object_or_404(model, pk=profile_id)


@require_POST
def alert_policy_save(request):
    next_url = _safe_next(request, request.POST.get('next'), reverse('alert_list'))
    scope, profile = _policy_scope_from_post(request)
    mode = request.POST.get('mode', '')
    name = request.POST.get('name', '').strip() or '默认告警策略'
    channel_ids = []
    invalid_channels = False
    for value in request.POST.getlist('channels'):
        try:
            channel_ids.append(UUID(value))
        except (ValueError, TypeError):
            invalid_channels = True
    channels = list(AlertChannel.objects.filter(pk__in=channel_ids).order_by('name', 'pk'))
    try:
        if mode not in AlertPolicy.Mode.values:
            raise ValidationError('告警策略模式无效。')
        if invalid_channels or {channel.pk for channel in channels} != set(channel_ids):
            raise ValidationError('渠道选择无效，请重新选择已有渠道。')
        if scope == 'default' and len(name) > 255:
            raise ValidationError('策略名称不能超过 255 个字符。')
        with transaction.atomic():
            if scope == 'default':
                policy, _created = AlertPolicy.objects.get_or_create(
                    default_slot=AlertPolicy.DEFAULT_SLOT,
                    defaults={'name': '默认告警策略', 'is_default': True, 'mode': AlertPolicy.Mode.OVERRIDE},
                )
                policy.name = name
                policy.is_default = True
                policy.mode = AlertPolicy.Mode.OVERRIDE
            elif isinstance(profile, InspectionProfile):
                policy, _created = AlertPolicy.objects.get_or_create(
                    inspection_profile=profile,
                    defaults={'name': f'{profile.name} 告警策略', 'mode': mode},
                )
                policy.mode = mode
            else:
                policy, _created = AlertPolicy.objects.get_or_create(
                    analysis_profile=profile,
                    defaults={'name': f'{profile.name} 告警策略', 'mode': mode},
                )
                policy.mode = mode
            policy.save()
            policy.channels.set(channels if policy.mode == AlertPolicy.Mode.OVERRIDE else [])
            policy.full_clean()
            policy.save()
    except ValidationError as exc:
        messages.error(request, '告警策略未保存，请检查配置。')
        return _failure_redirect(
            request, next_url, 'policy',
            state={'name': name[:255], 'mode': mode if mode in AlertPolicy.Mode.values else 'override',
                   'channels': [str(channel.pk) for channel in channels], 'errors': exc.messages},
            alert_policy_scope=scope, alert_policy_profile_id=str(profile.pk) if profile else '',
        )
    messages.success(request, f'已保存“{policy.name}”。')
    return _failure_redirect(request, next_url, 'policy',
                             state={'errors': [], 'name': policy.name, 'mode': policy.mode,
                                    'channels': [str(value) for value in policy.channels.values_list('pk', flat=True)]},
                             alert_policy_scope=scope,
                             alert_policy_profile_id=str(profile.pk) if profile else '')


@require_POST
def alert_test_send(request):
    next_url = _safe_next(request, request.POST.get('next'), reverse('alert_list'))
    channel = get_object_or_404(AlertChannel, pk=request.POST.get('channel_id', ''))
    message = AlertMessage(
        title='Alert channel test',
        text='This is a test of the saved alert channel configuration.',
        facts={'type': 'test'}, detail_url=reverse('alert_list'),
    )
    try:
        result = send_alert(channel, message)
    except Exception as exc:
        result = None
        summary = summarize(str(exc), *channel.settings.values())
    else:
        summary = summarize(result.response_summary, *channel.settings.values())
    success = bool(result and result.success)
    AlertTestSend.objects.create(
        channel=channel,
        status=AlertTestSend.Status.SENT if success else AlertTestSend.Status.FAILED,
        response_summary=summary,
        error_summary='' if success else summary,
    )
    messages.success(request, '测试发送已记录。' if success else '测试发送未成功，结果已记录。')
    return _failure_redirect(request, next_url, 'channel', state={'values': {}, 'errors': []},
                             alert_channel_type=channel.channel_type, alert_channel_id=str(channel.pk))
