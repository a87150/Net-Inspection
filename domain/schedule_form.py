from django import forms
from django.db import transaction
from django.utils import timezone
from net.models import Schedule
from net.inspections.schedules import next_run_at


class DomainSyncScheduleForm(forms.ModelForm):
    class Meta:
        model = Schedule
        fields = ('is_enabled', 'kind', 'interval_value', 'interval_unit', 'daily_time')
        labels = {'is_enabled': '启用定时同步', 'kind': '执行方式', 'interval_value': '间隔',
                  'interval_unit': '单位', 'daily_time': '每天执行时间'}
        widgets = {'daily_time': forms.TimeInput(attrs={'type': 'time'})}

    def __init__(self, *args, config, **kwargs):
        kwargs.setdefault('auto_id', 'domain-schedule-%s')
        super().__init__(*args, **kwargs)
        self.instance.domain_config = config
        for name, field in self.fields.items():
            field.widget.attrs['form'] = 'domain-sync-schedule-form'
            field.widget.attrs['class'] = 'form-check-input' if name == 'is_enabled' else 'form-control'
        self.fields['kind'].widget.attrs['data-schedule-kind'] = ''
        if self.instance._state.adding:
            self.initial.update(is_enabled=False, kind='interval', interval_value=60, interval_unit='minutes')

    def clean(self):
        data = super().clean()
        if data.get('kind') == 'interval':
            data['daily_time'] = None
        elif data.get('kind') == 'daily':
            data['interval_value'], data['interval_unit'] = None, ''
        if data.get('is_enabled') and not self.instance.domain_config.host:
            self.add_error('is_enabled', '请先保存域控连接设置。')
        return data

    @transaction.atomic
    def save(self, commit=True):
        schedule = super().save(commit=False)
        schedule.next_run_at = next_run_at(schedule, timezone.now()) if schedule.is_enabled else None
        schedule.full_clean()
        if commit:
            schedule.save()
        return schedule
