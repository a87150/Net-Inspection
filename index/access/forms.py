from django import forms

from net.access.adapters import V6600_V6000_COMPAT_VERSION
from net.models.access import AccessRecordSource


class AccessRecordSourceForm(forms.ModelForm):
    api_version = forms.ChoiceField(
        label='API 契约',
        choices=((V6600_V6000_COMPAT_VERSION, 'V6000 2.11 兼容格式（需按本机文档核实）'),),
        help_text='当前仅提供该显式兼容模式；它不是所有 V6600 版本的默认接口声明。',
    )

    credential = forms.CharField(
        label='API 令牌', required=False, strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
        help_text='留空可保留已保存的令牌；令牌不会显示、导出或写入任务快照。',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['platform'].initial = AccessRecordSource.Platform.ZKTECO_V6600
        for field in self.fields.values():
            field.widget.attrs['class'] = ('form-check-input' if isinstance(field.widget, forms.CheckboxInput)
                else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    class Meta:
        model = AccessRecordSource
        fields = ('name', 'platform', 'api_version', 'base_url', 'event_path',
                  'authentication_mode', 'authentication_name', 'username', 'verify_ssl',
                  'is_enabled', 'security_device', 'default_lookback_minutes')
        labels = {'name':'配置名称','platform':'门禁管理平台','base_url':'平台地址','event_path':'门禁事件接口路径',
            'authentication_mode':'令牌传递方式','authentication_name':'令牌参数名称','username':'平台用户名（可选）',
            'verify_ssl':'验证 HTTPS 证书','is_enabled':'启用此平台','security_device':'关联门禁设备（可选）',
            'default_lookback_minutes':'首次回溯分钟数'}
        widgets = {
            'base_url': forms.URLInput(attrs={'placeholder': 'https://platform.example:port'}),
            'event_path': forms.TextInput(attrs={'placeholder': '/按实例 API 文档填写'}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        credential = self.cleaned_data.get('credential')
        if credential:
            instance.set_credentials({'access_token': credential})
        if commit:
            instance.save()
        return instance
