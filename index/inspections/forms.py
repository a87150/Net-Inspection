"""Allowlisted Web forms for task profiles, schedules and manual runs."""

from django import forms
from django.core.exceptions import ValidationError

from net.models import ComputerAnalysisProfile, InspectionProfile, Schedule
from net.inspections.selection import (
    LINUX_FIELDS,
    NETWORK_FIELDS, NETWORK_FUNCTION_ITEMS,
    SECURITY_FIELDS,
    WINDOWS_FIELDS,
)
from net.devices.pc.analysis import ANALYSIS_ITEMS
from index.devices.pc.software_policy import validate_software_policy_upload


_INSPECTION_LABELS = {
    'computer_name': '设备名称', 'system_info': '系统信息', 'cpu': 'CPU',
    'memory': '内存', 'storage_status': '存储', 'network_info': '网络',
    'services': '服务状态', 'logs': '系统日志', 'device_info': '设备信息',
    'temperature': '温度', 'interface_status': '接口状态',
    'traffic': '实时接口流量（SNMP）',
    'vlan_status': 'VLAN 状态', 'status_data': '设备状态',
    'channel_status': '通道状态',
    'config_info': '设备配置（只读、脱敏，非完整恢复备份）',
}
_INSPECTION_LABELS.update(NETWORK_FUNCTION_ITEMS)
_ANALYSIS_LABELS = {
    'activation': 'Windows 激活', 'software': '已安装软件',
    'processes': '运行进程', 'bitlocker': 'BitLocker',
    'defender': 'Defender 信息', 'patches': '系统更新',
    'resource': '资源使用情况',
    'disk': '磁盘空间',
    'event_findings': '事件发现', 'system': '系统版本',
    'uptime': '连续开机时间',
    'browser_extensions': '浏览器扩展', 'identity_match': '账号与电脑名匹配',
    'cpu_health': 'CPU 温度与频率', 'domain_trust': '域连接状态',
    'group_policy': '计算机与用户组策略',
}


def inspection_item_choices(device_type):
    """Return only collector keys that this project type can execute."""
    if device_type == InspectionProfile.DeviceType.NETWORK_DEVICE:
        from net.devices.network.sangfor import DEFAULT_ITEMS
        keys = set(NETWORK_FIELDS) | set(NETWORK_FUNCTION_ITEMS) | set(DEFAULT_ITEMS) | {'traffic'}
    elif device_type == InspectionProfile.DeviceType.SERVER:
        keys = set(LINUX_FIELDS) | set(WINDOWS_FIELDS)
    elif device_type == InspectionProfile.DeviceType.MONITOR:
        keys = set(SECURITY_FIELDS)
    else:
        keys = set()
    from net.inspections.issues import PROJECT_RULES
    api_labels = {key: value[1] for key, value in PROJECT_RULES['networks'].items()}
    return tuple((key, _INSPECTION_LABELS.get(key, api_labels.get(key, key))) for key in sorted(keys))


def analysis_item_choices():
    return tuple(
        (key, _ANALYSIS_LABELS.get(key, key)) for key in sorted(ANALYSIS_ITEMS)
    )


class _ScheduleFieldsMixin:
    schedule_enabled = forms.BooleanField(required=False, label='启用定时执行')
    schedule_kind = forms.ChoiceField(
        required=False,
        choices=Schedule.Kind.choices,
        label='执行方式',
    )
    interval_value = forms.IntegerField(required=False, min_value=1, label='间隔数值')
    interval_unit = forms.ChoiceField(
        required=False,
        choices=Schedule.IntervalUnit.choices,
        label='间隔单位',
    )
    daily_time = forms.TimeField(required=False, label='每天执行时间')

    def _clean_schedule(self):
        cleaned = self.cleaned_data
        if not cleaned.get('schedule_enabled'):
            return
        kind = cleaned.get('schedule_kind')
        if kind == Schedule.Kind.INTERVAL:
            if cleaned.get('interval_value') is None:
                self.add_error('interval_value', '间隔计划必须设置间隔数值。')
            if not cleaned.get('interval_unit'):
                self.add_error('interval_unit', '间隔计划必须选择分钟或小时。')
        elif kind == Schedule.Kind.DAILY:
            if cleaned.get('daily_time') is None:
                self.add_error('daily_time', '每日计划必须设置执行时间。')
        else:
            self.add_error('schedule_kind', '请选择间隔执行或每天执行。')


class InspectionProfileConfigForm(_ScheduleFieldsMixin, forms.Form):
    target_rule_mode = forms.ChoiceField(required=False, initial='all', label='定时目标范围',
        choices=(('all', '全部设备'), ('selected', '指定设备'), ('filtered', '按条件精确匹配')))
    target_rule_ids = forms.MultipleChoiceField(required=False, label='指定设备',
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 5}))
    profile_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(max_length=255, label='配置名称')
    selected_items = forms.MultipleChoiceField(
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={'invalid_choice': '不支持的巡检项目。'},
        label='巡检项目',
    )
    timeout_seconds = forms.IntegerField(min_value=1, max_value=3600, label='超时（秒）')
    concurrent_workers = forms.IntegerField(min_value=1, max_value=64, label='并发数')
    schedule_enabled = forms.BooleanField(required=False, label='启用定时执行')
    schedule_kind = forms.ChoiceField(required=False, choices=Schedule.Kind.choices, label='执行方式')
    interval_value = forms.IntegerField(required=False, min_value=1, label='间隔数值')
    interval_unit = forms.ChoiceField(required=False, choices=Schedule.IntervalUnit.choices, label='间隔单位')
    daily_time = forms.TimeField(required=False, label='每天执行时间')

    def __init__(self, *args, device_type, instance=None, schedule=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.device_type = device_type
        self.instance = instance
        from net.inspections.schedules import _ASSET_MODELS, target_rule_fields
        target_devices = list(_ASSET_MODELS[device_type].objects.order_by('pk'))
        self.fields['target_rule_ids'].choices = [(str(obj.pk), str(obj)) for obj in target_devices]
        from net.devices.collection_profiles import collection_settings_for_assets, supported_collection_items
        from net.inspections.issues import DEVICE_PROJECTS
        kind = DEVICE_PROJECTS[device_type]
        effective = collection_settings_for_assets(kind,target_devices)
        self.target_device_options = [
            {
                'id': str(obj.pk),
                'items': supported_collection_items(kind,obj,effective[str(obj.pk)]),
                'label': str(obj),
                'vendor': str(getattr(obj, 'vendor', None) or getattr(obj, 'manufacturer', None) or '未填写'),
                'device_type': str(getattr(obj, 'device_type', None) or getattr(obj, 'server_type', None) or '未分类'),
            }
            for obj in target_devices
        ]
        self.target_device_vendors = sorted({option['vendor'] for option in self.target_device_options})
        self.target_device_types = sorted({option['device_type'] for option in self.target_device_options})
        self.rule_fields = target_rule_fields(device_type)
        for key, (model_field, label) in self.rule_fields.items():
            if model_field.get_internal_type() == 'BooleanField':
                field = forms.TypedChoiceField(required=False, choices=(('', '不限'), ('true', '是'), ('false', '否')),
                                               coerce=lambda value: value == 'true', empty_value=None)
            else:
                field = model_field.formfield(required=False)
                field.initial = None
                if isinstance(field, forms.ChoiceField):
                    field.choices = [('', '不限')] + [(key, label) for key, label in field.choices if key != '']
            field.label = label
            field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            self.fields['rule_' + key] = field
        selector = instance.target_selector if instance else {}
        self.initial.update({'target_rule_mode': selector.get('mode', 'all'),
                             'target_rule_ids': selector.get('target_ids', [])})
        for key, value in selector.get('filters', {}).items():
            self.initial['rule_' + key] = str(value).lower() if isinstance(value, bool) else value
        self.fields['selected_items'].choices = inspection_item_choices(device_type)
        if instance is not None and not self.is_bound:
            self.initial.update({
                'profile_id': instance.pk,
                'name': instance.name,
                'selected_items': instance.selected_items,
                'timeout_seconds': instance.timeout_seconds,
                'concurrent_workers': instance.concurrent_workers,
            })
        if schedule is not None and not self.is_bound:
            self.initial.update({
                'schedule_enabled': schedule.is_enabled,
                'schedule_kind': schedule.kind,
                'interval_value': schedule.interval_value,
                'interval_unit': schedule.interval_unit,
                'daily_time': schedule.daily_time,
            })
        from index.common.form_examples import apply_field_examples
        from index.devices.parameter_examples import PROFILE_EXAMPLES
        apply_field_examples(self, PROFILE_EXAMPLES)

    def clean(self):
        cleaned = super().clean()
        self._clean_schedule()
        mode = cleaned.get('target_rule_mode') or 'all'
        selector = {'mode': mode}
        if mode == 'selected':
            selector['target_ids'] = cleaned.get('target_rule_ids', [])
        elif mode == 'filtered':
            selector['filters'] = {key: cleaned['rule_' + key] for key in self.rule_fields
                                   if cleaned.get('rule_' + key) not in (None, '')}
        if mode != 'all' and not self.errors:
            from net.inspections.schedules import _selected_target_ids
            candidate = InspectionProfile(device_type=self.device_type, target_selector=selector)
            try:
                _selected_target_ids(candidate)
            except ValidationError as exc:
                self.add_error('target_rule_mode', '；'.join(exc.messages))
        if not self.errors and self.target_device_options:
            scoped_ids = None
            if mode == 'selected':
                scoped_ids = set(cleaned.get('target_rule_ids', []))
            elif mode == 'filtered':
                from net.inspections.schedules import _selected_target_ids
                scoped_ids = set(_selected_target_ids(InspectionProfile(device_type=self.device_type,target_selector=selector)))
            options = [option for option in self.target_device_options if scoped_ids is None or option['id'] in scoped_ids]
            available = {item for option in options for item in option['items']}
            if set(cleaned.get('selected_items', [])) - available:
                self.add_error('selected_items','所选设备不支持部分巡检项目，请按当前设备重新选择。')
        cleaned['target_selector'] = selector
        return cleaned

    @property
    def target_filter_fields(self):
        return [self['rule_' + key] for key in self.rule_fields]

    def profile_values(self):
        return {
            'name': self.cleaned_data['name'],
            'device_type': self.device_type,
            'target_selector': self.cleaned_data['target_selector'],
            'selected_items': self.cleaned_data['selected_items'],
            'timeout_seconds': self.cleaned_data['timeout_seconds'],
            'concurrent_workers': self.cleaned_data['concurrent_workers'],
        }


class ComputerAnalysisProfileConfigForm(_ScheduleFieldsMixin, forms.Form):
    matching_mode = forms.ChoiceField(required=False, initial='logs', label='人员匹配方式', choices=(
        ('logs', '不按人员匹配（日志为主）'), ('people', '按人员匹配（人员为主）')),
        help_text='人员为主：保留无日志人员；日志为主：保留未匹配人员的日志。工号优先，其次唯一姓名。')
    profile_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(max_length=255, label='配置名称')
    analysis_items = forms.MultipleChoiceField(
        choices=analysis_item_choices(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={'invalid_choice': '不支持的分析项目。'},
        label='分析项目',
    )
    software_policy_file = forms.FileField(
        required=False,
        label='上传软件策略文件',
        help_text='支持 UTF-8 编码的 .ini 文件，最大 1 MB；不重新上传会保留当前策略。',
        validators=[validate_software_policy_upload],
        widget=forms.FileInput(attrs={'accept': '.ini,text/plain'}),
    )
    minimum_windows_release = forms.CharField(
        required=False, max_length=16, label='最低 Windows 版本',
    )
    defender_update_max_days = forms.IntegerField(
        required=False, min_value=1, max_value=3650, label='Defender 病毒库最大间隔（天）',
    )
    defender_scan_max_days = forms.IntegerField(
        required=False, min_value=1, max_value=3650, label='Defender 扫描最大间隔（天）',
    )
    patch_max_days = forms.IntegerField(
        required=False, min_value=1, max_value=3650, label='系统补丁最大间隔（天）',
    )
    uptime_max_hours = forms.IntegerField(
        required=False, min_value=1, max_value=87600, label='最长连续开机时间（小时）',
    )
    disk_max_percent = forms.IntegerField(required=False, min_value=1, max_value=100, label='磁盘使用率报警阈值（%）')
    cpu_max_percent = forms.IntegerField(
        required=False, min_value=1, max_value=100, label='CPU 报警阈值（%）',
    )
    memory_max_percent = forms.IntegerField(
        required=False, min_value=1, max_value=100, label='内存报警阈值（%）',
    )
    cpu_temperature_max_celsius = forms.IntegerField(
        required=False, min_value=1, max_value=150, label='CPU 温度阈值（℃）',
    )
    site_ip_prefixes = forms.JSONField(required=False, label='IP 网段与站点映射',
                                      help_text='JSON 对象，例如 {"10.10.0.0/16": "长沙"}。')
    kms_servers_text = forms.CharField(
        required=False, widget=forms.Textarea, label='KMS 服务器',
    )
    concurrent_workers = forms.IntegerField(min_value=1, max_value=64, label='并发数')
    schedule_enabled = forms.BooleanField(required=False, label='启用定时执行')
    schedule_kind = forms.ChoiceField(required=False, choices=Schedule.Kind.choices, label='执行方式')
    interval_value = forms.IntegerField(required=False, min_value=1, label='间隔数值')
    interval_unit = forms.ChoiceField(required=False, choices=Schedule.IntervalUnit.choices, label='间隔单位')
    daily_time = forms.TimeField(required=False, label='每天执行时间')

    def __init__(self, *args, instance=None, schedule=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance is not None and not self.is_bound:
            self.initial.update({
                'profile_id': instance.pk,
                'matching_mode': getattr(instance, 'matching_mode', 'logs'),
                'name': instance.name,
                'analysis_items': instance.analysis_items,
                'minimum_windows_release': instance.minimum_windows_release,
                'defender_update_max_days': instance.defender_update_max_days,
                'defender_scan_max_days': instance.defender_scan_max_days,
                'patch_max_days': instance.patch_max_days,
                'uptime_max_hours': instance.uptime_max_hours,
                'disk_max_percent': instance.disk_max_percent,
                'cpu_max_percent': instance.cpu_max_percent,
                'memory_max_percent': instance.memory_max_percent,
                'cpu_temperature_max_celsius': instance.cpu_temperature_max_celsius,
                'site_ip_prefixes': instance.site_ip_prefixes,
                'kms_servers_text': '\n'.join(instance.kms_servers),
                'concurrent_workers': instance.concurrent_workers,
            })
        if schedule is not None and not self.is_bound:
            self.initial.update({
                'schedule_enabled': schedule.is_enabled,
                'schedule_kind': schedule.kind,
                'interval_value': schedule.interval_value,
                'interval_unit': schedule.interval_unit,
                'daily_time': schedule.daily_time,
            })
        from index.common.form_examples import apply_field_examples
        from index.devices.parameter_examples import PROFILE_EXAMPLES
        apply_field_examples(self, PROFILE_EXAMPLES)

    def clean_kms_servers_text(self):
        return [
            line.strip()
            for line in self.cleaned_data['kms_servers_text'].splitlines()
            if line.strip()
        ]

    def _configured_value(self, name, default):
        if name in self.data and self.cleaned_data.get(name) is not None:
            return self.cleaned_data[name]
        return getattr(self.instance, name, default) if self.instance is not None else default

    def clean(self):
        cleaned = super().clean()
        self._clean_schedule()
        if cleaned.get('schedule_enabled'):
            from net.models import PCLogSourceConfig
            if PCLogSourceConfig.load() is None:
                self.add_error('schedule_enabled', '请先保存日志来源，再启用定时执行。')
        return cleaned

    def profile_values(self):
        cleaned = self.cleaned_data
        return {
            'name': cleaned['name'],
            'analysis_items': cleaned['analysis_items'],
            'matching_mode': self._configured_value('matching_mode', 'logs') or 'logs',
            'minimum_windows_release': self._configured_value('minimum_windows_release', '23H2'),
            'defender_update_max_days': self._configured_value('defender_update_max_days', 7),
            'defender_scan_max_days': self._configured_value('defender_scan_max_days', 7),
            'patch_max_days': self._configured_value('patch_max_days', 30),
            'uptime_max_hours': self._configured_value('uptime_max_hours', 168),
            'disk_max_percent': self._configured_value('disk_max_percent', 90),
            'cpu_max_percent': self._configured_value('cpu_max_percent', 90),
            'memory_max_percent': self._configured_value('memory_max_percent', 90),
            'cpu_temperature_max_celsius': self._configured_value('cpu_temperature_max_celsius', 85) or 85,
            'site_ip_prefixes': self._configured_value('site_ip_prefixes', {}) or {},
            'kms_servers': (
                cleaned['kms_servers_text']
                if 'kms_servers_text' in self.data
                else list(getattr(self.instance, 'kms_servers', []))
            ),
            'concurrent_workers': cleaned['concurrent_workers'],
        }


class ManualTaskForm(forms.Form):
    profile_id = forms.UUIDField()
    target_mode = forms.ChoiceField(
        choices=(
            ('all', '全部资产'),
            ('selected', '已选资产'),
            ('filtered', '当前筛选结果'),
            ('fetch', '获取并分析远程日志'),
        ),
    )
    selected_items = forms.MultipleChoiceField(
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={'invalid_choice': '所选项目不在当前配置的允许范围内。'},
    )
    concurrent_workers = forms.IntegerField(
        required=False, min_value=1, max_value=64,
    )
    next = forms.CharField(required=False)

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile
        if isinstance(profile, InspectionProfile):
            choices = inspection_item_choices(profile.device_type)
            enabled = set(profile.selected_items)
        else:
            choices = analysis_item_choices()
            enabled = set(profile.analysis_items)
        self.fields['selected_items'].choices = [
            choice for choice in choices if choice[0] in enabled
        ]

    def clean_target_mode(self):
        mode = self.cleaned_data['target_mode']
        if isinstance(self.profile, InspectionProfile) and mode == 'fetch':
            raise ValidationError('设备巡检不支持扫描模式。')
        if isinstance(self.profile, ComputerAnalysisProfile) and mode not in {
            'all', 'selected', 'filtered', 'fetch',
        }:
            raise ValidationError('计算机分析目标范围无效。')
        return mode
