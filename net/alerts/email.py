"""SMTP delivery adapter with explicit TLS or SSL selection."""

from email.message import EmailMessage
import smtplib
import ssl

from .base import SMTP_TIMEOUT, DeliveryResult, disabled_result, summarize


def _smtp_retryable(exc):
    if isinstance(exc, (TimeoutError, smtplib.SMTPServerDisconnected)):
        return True
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    return False


def _message(message, settings):
    email = EmailMessage()
    email['Subject'] = message.title
    email['From'] = settings['from_email']
    email['To'] = ', '.join(settings['recipients'])
    email.set_content(f'{message.text}\n\nDetails: {message.detail_url}')
    return email


def send_email(channel, message, *, smtp_factory=None):
    """Send one email alert, choosing SSL-on-connect or STARTTLS exclusively."""
    if not getattr(channel, 'is_enabled', False):
        return disabled_result()

    settings = channel.settings
    use_tls = settings.get('use_tls', False)
    use_ssl = settings.get('use_ssl', False)
    password = settings.get('password', '')
    if use_tls and use_ssl:
        return DeliveryResult(False, 'Email configuration cannot enable TLS and SSL together.', False)

    client = None
    try:
        if use_ssl:
            factory = smtp_factory or smtplib.SMTP_SSL
            client = factory(
                settings['smtp_host'],
                settings['smtp_port'],
                timeout=SMTP_TIMEOUT,
                context=ssl.create_default_context(),
            )
        else:
            factory = smtp_factory or smtplib.SMTP
            client = factory(settings['smtp_host'], settings['smtp_port'], timeout=SMTP_TIMEOUT)
            if use_tls:
                client.starttls(context=ssl.create_default_context())
        if settings.get('username'):
            client.login(settings['username'], password)
        refused = client.send_message(
            _message(message, settings),
            from_addr=settings['from_email'],
            to_addrs=settings['recipients'],
        )
        if refused:
            retryable = any(400 <= code < 500 for code, _ in refused.values())
            return DeliveryResult(False, summarize(refused, password), retryable)
        return DeliveryResult(True, 'Email accepted by SMTP server.', False)
    except (TimeoutError, smtplib.SMTPException, OSError) as exc:
        return DeliveryResult(False, summarize(f'SMTP delivery failed: {exc}', password), _smtp_retryable(exc))
    finally:
        if client is not None:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                pass
