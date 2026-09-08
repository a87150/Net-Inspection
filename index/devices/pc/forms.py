from pathlib import Path
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from net.models import PCLogSourceConfig, TaskRun
from net.devices.pc.configuration import SOURCE_FIELDS
from net.devices.pc.connectors.base import normalize_path, within
from net.devices.pc.credentials import load_pc_source_secret, store_pc_source_secret
from net.secret_masks import MASKED_SECRET, MaskedSecretInput


class PCLogSourceForm(forms.ModelForm):
    password = forms.CharField(required=False, label='连接密码',
                               widget=MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'}),
                               help_text='已保存密码会显示为星号；保留星号或留空均会保留原密码。')

    class Meta:
        model = PCLogSourceConfig
        fields = (*SOURCE_FIELDS, 'password')
        labels = {
            'source_type': '获取协议', 'host': '服务器地址', 'port': '端口',
            'smb_auth_mode': '共享访问身份',
            'username': '连接账号', 'domain': '域（仅 SMB）', 'share_name': '共享名称（仅 SMB）',
            'remote_root_directory': '远程根目录', 'remote_incoming_directory': '汇总日志目录',
            'remote_processed_directory': '已处理目录', 'remote_failed_directory': '失败目录',
            'local_staging_directory': '服务器本地暂存目录',
            'terminal_windows_path': 'Windows 终端写入路径', 'terminal_macos_path': 'macOS 终端挂载路径',
            'recursive': '包含子目录', 'file_time_mode': '文件时间范围', 'recent_days': '最近 N 天',
            'range_start_date': '开始日期', 'range_end_date': '结束日期',
            'ftp_passive': 'FTP 被动模式', 'ftp_use_tls': '启用 FTPS（验证证书并加密数据连接）',
        }
        help_texts = {
            'remote_root_directory': 'SMB 为共享内相对路径；FTP 可填写服务器绝对目录，如 /logs。',
            'remote_incoming_directory': '相对于远程根目录，例如 incoming。所有 PC 日志汇总到这里。',
            'remote_processed_directory': '相对于远程根目录；成功读取的有效日志（包含分析异常）移到这里。',
            'remote_failed_directory': '相对于远程根目录；JSON 或必需字段无效的日志移到这里。',
            'local_staging_directory': 'Worker 所在服务器的绝对路径；只用于临时下载。',
            'terminal_windows_path': r'例如 \\server\share\incoming；终端使用自身权限访问。',
            'terminal_macos_path': '例如 /Volumes/Logs/incoming；请先挂载共享目录。',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget = forms.TextInput()
            if name in ('range_start_date', 'range_end_date'):
                field.widget = forms.DateInput(attrs={'type': 'date'})
            field.widget.attrs['class'] = ('form-check-input' if isinstance(field.widget, forms.CheckboxInput)
                                          else 'form-select' if isinstance(field.widget, forms.Select)
                                          else 'form-control')
        if self.instance and self.instance.pk:
            try:
                has_password = bool(load_pc_source_secret(self.instance))
            except ValidationError:
                has_password = False
            self.initial['password'] = MASKED_SECRET if has_password else ''
        self.fields['port'].min_value = 1
        self.fields['port'].max_value = 65535

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('source_type') == 'ftp' or cleaned.get('smb_auth_mode') == 'credentials':
            if not cleaned.get('username'):
                self.add_error('username', '请填写连接账号。')
        elif cleaned.get('source_type') == 'smb':
            cleaned['username'] = cleaned['domain'] = ''
            if cleaned.get('port') != 445:
                self.add_error('port', '使用 Windows 运行账号时端口必须为 445。')
        if not 1 <= (cleaned.get('port') or 0) <= 65535:
            self.add_error('port', '端口必须在 1–65535 之间。')
        kind = cleaned.get('source_type')
        if kind == 'ftp':
            cleaned['domain'] = ''
            cleaned['share_name'] = ''
        elif kind == 'smb':
            for key in ('host', 'share_name'):
                if any(c in cleaned.get(key, '') for c in '/\\:\r\n\x00'):
                    self.add_error(key, '只能填写主机或共享名称，不能包含路径。')
        for key in ('remote_incoming_directory', 'remote_processed_directory', 'remote_failed_directory'):
            try:
                cleaned[key] = normalize_path(cleaned.get(key, ''))
                if not cleaned[key]:
                    if key == 'remote_incoming_directory' and getattr(self, 'allow_root_directory', False):
                        cleaned[key] = '.'
                    else:
                        self.add_error(key, '请填写相对目录。')
            except ValueError:
                self.add_error(key, '必须填写不含上级跳转的相对目录。')
        try:
            root = cleaned.get('remote_root_directory', '').replace('\\', '/')
            normalize_path(root.lstrip('/') if kind == 'ftp' else root)
        except ValueError:
            self.add_error('remote_root_directory', '远程根目录格式无效。')
        incoming = cleaned.get('remote_incoming_directory')
        processed = cleaned.get('remote_processed_directory')
        failed = cleaned.get('remote_failed_directory')
        if incoming and processed and failed:
            if within(incoming, processed) or within(incoming, failed):
                self.add_error('remote_incoming_directory', '汇总目录不能位于归档目录内或与其相同。')
            if within(processed, failed) or within(failed, processed):
                self.add_error('remote_failed_directory', '两个归档目录不能重叠。')
        staging = cleaned.get('local_staging_directory', '')
        if not staging or not Path(staging).is_absolute():
            self.add_error('local_staging_directory', '请填写 Worker 服务器上的绝对目录。')
        if cleaned.get('file_time_mode') == 'recent_days':
            cleaned['range_start_date'] = cleaned['range_end_date'] = None
        else:
            cleaned['recent_days'] = None
        return cleaned

    def save(self, commit=True):
        if not commit:
            raise ValueError('日志来源及密码必须一起保存。')
        with transaction.atomic():
            previous = PCLogSourceConfig.objects.select_for_update().filter(pk=1).first()
            if TaskRun.objects.filter(task_type='computer_fetch', status__in=TaskRun.ACTIVE_STATUSES).exists():
                raise ValidationError('有日志获取任务正在排队或执行，请结束任务后再修改来源。')
            password = self.cleaned_data.get('password')
            system_identity = self.cleaned_data.get('source_type') == 'smb' and self.cleaned_data.get('smb_auth_mode') == 'system'
            if not system_identity and (not password or password == MASKED_SECRET):
                if previous is None:
                    raise ValidationError('首次配置必须填写连接密码。')
                password = load_pc_source_secret(previous)
            source = super().save(commit=False)
            source.pk = 1
            source.last_tested_at = None
            source.last_test_error = ''
            source.save()
            if not system_identity:
                store_pc_source_secret(source, password)
            return source
