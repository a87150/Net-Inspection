"""Transport adapters and normalized messages for alert delivery."""

from .base import AlertMessage, DeliveryResult, redact_secret
from .dingtalk import send_dingtalk
from .email import send_email
from .feishu import send_feishu
from .messages import build_alert_message


def send_alert(channel, message, *, http_post=None, smtp_factory=None, now=None):
    """Send one normalized alert through one saved channel.

    Callers may inject the HTTP or SMTP boundary.  Production dispatch and
    persistence are deliberately owned by the later delivery service.
    """
    if not getattr(channel, 'is_enabled', False):
        return DeliveryResult(False, 'Alert channel is disabled.', False)

    channel_type = getattr(channel, 'channel_type', '')
    if channel_type == 'feishu':
        return send_feishu(channel, message, http_post=http_post, now=now)
    if channel_type == 'dingtalk':
        return send_dingtalk(channel, message, http_post=http_post, now=now)
    if channel_type == 'email':
        return send_email(channel, message, smtp_factory=smtp_factory)
    return DeliveryResult(False, 'Unsupported alert channel.', False)


__all__ = [
    'AlertMessage',
    'DeliveryResult',
    'build_alert_message',
    'redact_secret',
    'send_alert',
    'send_dingtalk',
    'send_email',
    'send_feishu',
]
