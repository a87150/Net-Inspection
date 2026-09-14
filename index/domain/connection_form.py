from django import forms

from net.models import Domain_Controller_Config
from net.secret_masks import MaskedSecretInput


class DomainInactivityForm(forms.Form):
    inactive_days = forms.IntegerField(
        label='未登录天数', min_value=1, max_value=36500, initial=60,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1'}),
    )


class DomainControllerConfigForm(forms.ModelForm):
    class Meta:
        model = Domain_Controller_Config
        fields = [
            'name', 'host', 'port', 'use_ssl', 'base_dn', 'bind_username',
            'bind_password', 'user_filter', 'computer_filter',
            'group_filter',
        ]
        widgets = {
            'bind_password': MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
            'user_filter': forms.TextInput(),
            'computer_filter': forms.TextInput(),
            'group_filter': forms.TextInput(),
        }
        labels = {
            'name': '配置名称', 'host': '域控服务器', 'port': 'LDAP 端口',
            'use_ssl': '使用 LDAPS', 'base_dn': '搜索根目录',
            'bind_username': '连接账号', 'bind_password': '连接密码',
            'user_filter': '账号过滤器', 'computer_filter': '计算机过滤器',
            'group_filter': '分组过滤器',
        }
        help_texts = {
            'port': '普通 LDAP 通常使用 389；勾选 LDAPS 时通常使用 636。',
            'use_ssl': '这里表示建立连接时直接使用 TLS（隐式 LDAPS），不是 389 端口的 StartTLS。',
            'bind_username': 'DOMAIN\\user 使用 NTLM；user@example.com 或完整 DN 使用 SIMPLE；只填用户名时按 Base DN 补成 UPN。不会自动反复尝试凭据。',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'

    def clean(self):
        cleaned = super().clean()
        required = ['host', 'base_dn', 'bind_username', 'bind_password']
        for name in required:
            if not cleaned.get(name):
                self.add_error(name, '该字段不能为空。')
        if cleaned.get('use_ssl') and cleaned.get('port') == 389:
            self.add_error('port', '389 端口不能使用隐式 LDAPS；请改为 636，或取消“使用 LDAPS”。')
        return cleaned
