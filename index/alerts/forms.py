"""Secret-safe alert-channel configuration forms.

The forms only receive a secret replacement.  Existing credentials are never
placed in initial data, rendered HTML, or validation messages.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from net.models import AlertChannel
from net.secret_masks import MASKED_SECRET, MaskedSecretInput


class _AlertChannelForm(forms.Form):
    channel_type = None
    secret_field_name = None

    name = forms.CharField(max_length=255, label='渠道名称')
    is_enabled = forms.BooleanField(required=False, label='启用渠道')

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance or AlertChannel(channel_type=self.channel_type)
        self.existing_settings = dict(self.instance.settings or {})
        if not self.is_bound:
            self.initial.update({
                'name': self.instance.name,
                'is_enabled': self.instance.is_enabled,
            })
            self._set_safe_initials()
        for name in ('webhook_url', 'secret', 'password'):
            if self.existing_settings.get(name):
                self.initial[name] = MASKED_SECRET
            else:
                self.initial.pop(name, None)

    def _set_safe_initials(self):
        """Subclasses expose only non-secret settings in initial form data."""

    def build_settings(self, cleaned):
        raise NotImplementedError

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        settings = self.build_settings(cleaned)
        candidate = AlertChannel(
            pk=self.instance.pk,
            name=cleaned.get('name', ''),
            is_enabled=cleaned.get('is_enabled', False),
            channel_type=self.channel_type,
            settings=settings,
        )
        candidate._state.adding = self.instance._state.adding
        candidate._state.db = self.instance._state.db
        try:
            candidate.full_clean()
        except ValidationError:
            # The model validators deliberately avoid values in messages; a
            # generic error makes that boundary explicit for every form.
            self.add_error(None, '渠道配置无效，请检查地址、邮箱或 TLS 设置。')
        else:
            cleaned['settings'] = settings
        return cleaned

    def _secret_or_existing(self, cleaned, key):
        replacement = cleaned.get(self.secret_field_name, '')
        if replacement and replacement != MASKED_SECRET:
            return replacement
        return self.existing_settings.get(key, '')

    def save(self):
        if not self.is_valid():
            raise ValueError('不能保存无效的告警渠道表单。')
        self.instance.name = self.cleaned_data['name']
        self.instance.is_enabled = self.cleaned_data['is_enabled']
        self.instance.channel_type = self.channel_type
        self.instance.settings = self.cleaned_data['settings']
        self.instance.full_clean()
        self.instance.save()
        return self.instance


class FeishuAlertChannelForm(_AlertChannelForm):
    channel_type = AlertChannel.ChannelType.FEISHU
    secret_field_name = 'secret'

    webhook_url = forms.CharField(
        max_length=2048, required=False, widget=MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
        label='飞书 Webhook（星号或留空保持不变）',
    )
    secret = forms.CharField(
        required=False,
        widget=MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
        label='签名密钥（星号或留空保持不变）',
    )

    def clean_webhook_url(self):
        value = self.cleaned_data['webhook_url'].strip()
        if value == MASKED_SECRET:
            value = self.existing_settings.get('webhook_url', '')
        if not value and not self.instance._state.adding:
            value = self.existing_settings.get('webhook_url', '')
        if not value:
            raise ValidationError('新渠道必须填写 HTTPS Webhook 地址。')
        return value

    def build_settings(self, cleaned):
        return {
            'webhook_url': cleaned.get('webhook_url', '').strip(),
            'secret': self._secret_or_existing(cleaned, 'secret'),
        }


class DingTalkAlertChannelForm(FeishuAlertChannelForm):
    channel_type = AlertChannel.ChannelType.DINGTALK
    webhook_url = forms.CharField(
        max_length=2048, required=False, widget=MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
        label='钉钉 Webhook（星号或留空保持不变）',
    )


class EmailAlertChannelForm(_AlertChannelForm):
    channel_type = AlertChannel.ChannelType.EMAIL
    secret_field_name = 'password'

    smtp_host = forms.CharField(max_length=255, label='SMTP 主机')
    smtp_port = forms.IntegerField(min_value=1, max_value=65535, label='SMTP 端口')
    use_tls = forms.BooleanField(required=False, label='使用 TLS')
    use_ssl = forms.BooleanField(required=False, label='使用 SSL')
    username = forms.CharField(required=False, max_length=255, label='SMTP 账号')
    password = forms.CharField(
        required=False,
        widget=MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
        label='SMTP 密码（星号或留空保持不变）',
    )
    from_email = forms.EmailField(label='发件人邮箱')
    recipients = forms.CharField(label='收件人（逗号分隔）')

    def _set_safe_initials(self):
        for key in ('smtp_host', 'smtp_port', 'use_tls', 'use_ssl', 'username', 'from_email'):
            self.initial[key] = self.existing_settings.get(key)
        self.initial['recipients'] = ', '.join(self.existing_settings.get('recipients', []))

    def clean_recipients(self):
        values = [value.strip() for value in self.cleaned_data['recipients'].split(',') if value.strip()]
        if not values:
            raise ValidationError('至少需要一个收件人。')
        normalized = []
        for value in values:
            try:
                validate_email(value)
            except ValidationError as exc:
                raise ValidationError('收件人邮箱地址无效。') from exc
            normalized.append(value.casefold())
        if len(normalized) != len(set(normalized)):
            raise ValidationError('收件人邮箱地址不能重复。')
        return values

    def build_settings(self, cleaned):
        return {
            'smtp_host': cleaned.get('smtp_host', '').strip(),
            'smtp_port': cleaned.get('smtp_port'),
            'use_tls': cleaned.get('use_tls', False),
            'use_ssl': cleaned.get('use_ssl', False),
            'username': cleaned.get('username', '').strip(),
            'password': self._secret_or_existing(cleaned, 'password'),
            'from_email': cleaned.get('from_email', '').strip(),
            'recipients': cleaned.get('recipients', []),
        }
