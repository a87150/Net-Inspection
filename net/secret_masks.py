"""Template-safe fixed markers for credentials that are already stored."""

from django import forms


MASKED_SECRET = '••••••••'


def is_masked_secret(value):
    return value == MASKED_SECRET


class MaskedSecretInput(forms.PasswordInput):
    """Render only a fixed marker; never serialize an actual secret to HTML."""

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, render_value=True)

    def get_context(self, name, value, attrs):
        if value and not is_masked_secret(value):
            value = ''
        return super().get_context(name, value, attrs)
