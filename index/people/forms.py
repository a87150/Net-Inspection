"""Write-only forms for the two fixed personnel API providers."""

from django import forms

from net.people.providers import get_provider_definition, save_provider_source


class PeopleProviderForm(forms.Form):
    root_department_ids = forms.CharField(
        label='根部门 ID（逗号分隔）', required=False,
    )
    is_enabled = forms.BooleanField(label='启用此平台', required=False, initial=True)
    app_id = forms.CharField(
        label='App ID（只写）', required=False, widget=forms.PasswordInput,
    )
    app_key = forms.CharField(
        label='App Key（只写）', required=False, widget=forms.PasswordInput,
    )
    app_secret = forms.CharField(
        label='App Secret（只写）', required=False, widget=forms.PasswordInput,
    )

    def __init__(self, data=None, *, source=None, provider='feishu'):
        definition = get_provider_definition(provider)
        initial = {
            'root_department_ids': ', '.join(source.root_department_ids) if source else '',
            'is_enabled': source.is_enabled if source else True,
        }
        super().__init__(data=data, initial=initial, auto_id=f'{provider}_%s')
        self._source = source
        self._provider = provider
        self._definition = definition
        self.fields.pop('app_key' if provider == 'feishu' else 'app_id')
        if provider == 'feishu':
            self.fields['root_department_ids'].help_text = (
                '留空表示从飞书租户根部门 0 开始递归同步全部子部门。'
            )
        for name, field in self.fields.items():
            field.widget.attrs['class'] = (
                'form-check-input' if name == 'is_enabled' else 'form-control'
            )
            if name in definition.credential_fields:
                field.widget.attrs['autocomplete'] = 'new-password'
                field.help_text = (
                    '已配置；留空保留，填写则替换。'
                    if source else '仅用于保存，不会显示已存凭据。'
                )

    def clean(self):
        data = super().clean()
        roots = [
            value.strip()
            for value in data.get('root_department_ids', '').replace('，', ',').split(',')
            if value.strip()
        ]
        if len(roots) != len(set(roots)):
            self.add_error('root_department_ids', '根部门不能重复。')
        data['root_department_ids'] = roots
        stored = self._source.credentials if self._source else {}
        for name in self._definition.credential_fields:
            if not data.get(name) and not stored.get(name):
                self.add_error(name, '首次保存必须填写此凭据。')
        return data

    def save(self):
        return save_provider_source(self._provider, self.cleaned_data)

    def public_form(self):
        """Return a template-safe form with no submitted secret values."""
        errors = self.errors.copy() if self.is_bound else None
        safe_data = self.data.copy() if self.is_bound else None
        if safe_data is not None:
            for key in ('app_id', 'app_key', 'app_secret'):
                safe_data.pop(key, None)
        public = PeopleProviderForm(
            data=safe_data,
            source=self._source,
            provider=self._provider,
        )
        public.initial = dict(self.initial)
        if errors is not None:
            public._errors = errors
            public.cleaned_data = {}
        return public


# Temporary compatibility name until the old public workflow is removed.
PeopleSourceForm = PeopleProviderForm
