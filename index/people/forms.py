"""Explicit public source fields and write-only directory credentials."""

from django import forms
from django.core.exceptions import ValidationError

from net.models import PeopleSyncSource


class PeopleSourceForm(forms.Form):
    source_type = forms.ChoiceField(choices=PeopleSyncSource.SourceType.choices, widget=forms.HiddenInput)
    name = forms.CharField(label='来源名称', max_length=255)
    source_key = forms.RegexField(label='稳定来源标识', regex=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,190}$')
    root_department_ids = forms.CharField(label='根部门 ID（逗号分隔）', required=False)
    is_enabled = forms.BooleanField(label='启用此来源', required=False, initial=True)
    app_id = forms.CharField(label='App ID（只写）', required=False, widget=forms.PasswordInput)
    app_key = forms.CharField(label='App Key（只写）', required=False, widget=forms.PasswordInput)
    app_secret = forms.CharField(label='App Secret（只写）', required=False, widget=forms.PasswordInput)

    def __init__(self, data=None, *, source=None, provider='feishu'):
        public = source.public_data() if source else {'source_type': provider, 'is_enabled': True}
        initial = {key: value for key, value in public.items() if key in self.base_fields}
        initial['root_department_ids'] = ', '.join(public.get('root_department_ids', []))
        super().__init__(data=data, initial=initial, auto_id=f'{provider}_%s')
        self._source = source
        self._provider = provider
        self.fields.pop('app_key' if provider == 'feishu' else 'app_id')
        for name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-check-input' if name == 'is_enabled' else 'form-control'
            if name in {'app_id', 'app_key', 'app_secret'}:
                field.widget.attrs['autocomplete'] = 'new-password'
                field.help_text = '已配置；留空保留，填写则替换。' if source else '仅用于保存，不会显示已存凭据。'
        if source:
            self.fields['source_key'].widget.attrs['readonly'] = True

    def clean(self):
        data = super().clean()
        if data.get('source_type') != self._provider:
            raise ValidationError('不能更改来源平台。')
        if self._source and (self._source.source_type != self._provider
                             or data.get('source_key') != self._source.source_key):
            raise ValidationError('不能更改已有来源的稳定标识或平台；请新建来源。')
        roots = [value.strip() for value in data.get('root_department_ids', '').replace('，', ',').split(',') if value.strip()]
        if len(roots) != len(set(roots)):
            self.add_error('root_department_ids', '根部门不能重复。')
        data['root_department_ids'] = roots
        for name in ('app_id' if self._provider == 'feishu' else 'app_key', 'app_secret'):
            if not data.get(name) and not (self._source and self._source.credentials.get(name)):
                self.add_error(name, '新来源必须填写此凭据。')
        return data

    def save(self):
        source = self._source or PeopleSyncSource(source_type=self._provider)
        for key in ('name', 'source_key', 'root_department_ids', 'is_enabled'):
            setattr(source, key, self.cleaned_data[key])
        source.credentials = dict(source.credentials)
        for key in ('app_id' if self._provider == 'feishu' else 'app_key', 'app_secret'):
            if self.cleaned_data.get(key):
                source.credentials[key] = self.cleaned_data[key]
        source.full_clean()
        source.save()
        return source

    def public_form(self):
        """No source model or bound/cleaned secrets may enter a template context."""
        errors = self.errors.copy() if self.is_bound else None
        safe_data = self.data.copy() if self.is_bound else None
        if safe_data is not None:
            for key in ('app_id', 'app_key', 'app_secret'):
                safe_data.pop(key, None)
        public = PeopleSourceForm(data=safe_data, provider=self._provider)
        public.initial = dict(self.initial)
        public.fields = self.fields.copy()
        if errors is not None:
            public._errors = errors
            public.cleaned_data = {}
        return public
