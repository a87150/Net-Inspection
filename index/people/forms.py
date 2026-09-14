"""Write-only forms for the two fixed personnel API providers."""

from django import forms
from django.db import transaction
from django.utils import timezone

from net.inspections.schedules import next_run_at
from net.models import PeopleSyncSource, Schedule
from net.secret_masks import MASKED_SECRET, MaskedSecretInput
from net.people.providers import get_provider_definition, save_provider_source


class PeopleProviderForm(forms.Form):
    root_department_ids = forms.CharField(
        label='根部门 ID（逗号分隔）', required=False,
    )
    is_enabled = forms.BooleanField(label='启用此平台', required=False, initial=True)
    app_id = forms.CharField(
        label='App ID（只写）', required=False, widget=MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
    )
    app_key = forms.CharField(
        label='App Key（只写）', required=False, widget=MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
    )
    app_secret = forms.CharField(
        label='App Secret（只写）', required=False, widget=MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
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
        stored = source.credentials if source else {}
        for name, field in self.fields.items():
            field.widget.attrs['class'] = (
                'form-check-input' if name == 'is_enabled' else 'form-control'
            )
            if name in definition.credential_fields:
                field.widget.attrs['autocomplete'] = 'new-password'
                if stored.get(name):
                    self.initial[name] = MASKED_SECRET
                field.help_text = (
                    '已保存；保留星号或留空均可保留，填写则替换。'
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
            if data.get(name) == MASKED_SECRET:
                data[name] = ''
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
class PeopleScheduleForm(forms.Form):
    is_enabled = forms.BooleanField(label='启用自动同步', required=False)
    kind = forms.ChoiceField(label='执行方式', choices=Schedule.Kind.choices)
    interval_value = forms.IntegerField(label='间隔', min_value=1, required=False)
    interval_unit = forms.ChoiceField(
        label='单位', choices=(('', '请选择'), *Schedule.IntervalUnit.choices),
        required=False,
    )
    daily_time = forms.TimeField(
        label='每天执行时间', required=False, widget=forms.TimeInput(format='%H:%M'),
    )

    def __init__(self, data=None, *, source, schedule=None):
        initial = {
            'is_enabled': schedule.is_enabled if schedule else False,
            'kind': schedule.kind if schedule else Schedule.Kind.INTERVAL,
            'interval_value': schedule.interval_value if schedule else 30,
            'interval_unit': schedule.interval_unit if schedule else Schedule.IntervalUnit.MINUTES,
            'daily_time': schedule.daily_time if schedule else None,
        }
        super().__init__(data=data, initial=initial, auto_id=f'{source.source_type}_schedule_%s')
        self.source = source
        self.schedule = schedule
        stored = source.credentials if source else {}
        for name, field in self.fields.items():
            field.widget.attrs['class'] = (
                'form-check-input' if name == 'is_enabled' else 'form-control form-control-sm'
            )

    def clean(self):
        data = super().clean()
        if data.get('kind') == Schedule.Kind.INTERVAL:
            data['daily_time'] = None
            if data.get('interval_value') is None:
                self.add_error('interval_value', '间隔执行必须填写间隔。')
            if data.get('interval_unit') not in Schedule.IntervalUnit.values:
                self.add_error('interval_unit', '间隔执行必须选择分钟或小时。')
        elif data.get('kind') == Schedule.Kind.DAILY:
            data['interval_value'] = None
            data['interval_unit'] = ''
            if data.get('daily_time') is None:
                self.add_error('daily_time', '每天执行必须填写时间。')
        if data.get('is_enabled') and not (
            self.source.is_enabled
            and self.source.public_data()['connection_test_current']
        ):
            self.add_error('is_enabled', '启用自动同步必须先通过当前配置的连接测试。')
        return data

    def save(self):
        return save_people_schedule(self.source, self.cleaned_data)


@transaction.atomic
def save_people_schedule(source, cleaned_data):
    source = PeopleSyncSource.objects.select_for_update().get(pk=source.pk)
    schedule = (
        Schedule.objects.select_for_update().filter(people_source=source).first()
        or Schedule(people_source=source)
    )
    schedule.kind = cleaned_data['kind']
    schedule.interval_value = cleaned_data['interval_value']
    schedule.interval_unit = cleaned_data['interval_unit']
    schedule.daily_time = cleaned_data['daily_time']
    schedule.is_enabled = cleaned_data['is_enabled']
    schedule.next_run_at = next_run_at(schedule, timezone.now()) if schedule.is_enabled else None
    schedule.full_clean()
    schedule.save()
    return schedule
