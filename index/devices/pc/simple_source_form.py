"""Friendly inputs translated to the existing connection/transfer contract."""
import posixpath
import re

from django import forms
from django.conf import settings

from net.devices.pc.connectors.base import normalize_path
from .forms import PCLogSourceForm


def source_directory(source):
    if source is None:
        return ''
    return posixpath.join(source.remote_root_directory.replace('\\', '/'),
                          source.remote_incoming_directory).strip('/')


def shared_directory(source):
    if source is None or source.source_type != 'smb':
        return ''
    suffix = normalize_path(source_directory(source)).replace('/', '\\')
    return '\\\\' + source.host + '\\' + source.share_name + ('\\' + suffix if suffix else '')


class SimplePCLogSourceForm(PCLogSourceForm):
    allow_root_directory = True
    use_required_attribute = False
    shared_path = forms.CharField(
        label='共享文件夹完整路径', required=False,
        help_text=r'直接粘贴资源管理器里的路径，例如 \\192.168.1.10\PCLogs\incoming。',
        widget=forms.TextInput(attrs={'placeholder': r'\\服务器\共享文件夹\日志目录'}))
    ftp_directory = forms.CharField(
        label='FTP 日志目录', required=False,
        help_text='填写 FTP 中存放 JSON 的目录，例如 /logs/incoming；根目录填 /。',
        widget=forms.TextInput(attrs={'placeholder': '/logs/incoming'}))

    def __init__(self, data=None, *args, **kwargs):
        source = kwargs.get('instance')
        self.path_error = None
        if data is not None:
            data = data.copy()
            kind = data.get('source_type', 'smb')
            data['source_type'] = kind
            if not data.get('smb_auth_mode'):
                data['smb_auth_mode'] = 'system'
            same_kind = source is not None and source.source_type == kind
            defaults = {
                'port': source.port if same_kind else (445 if kind == 'smb' else 21),
                'file_time_mode': getattr(source, 'file_time_mode', 'recent_days'),
                'recent_days': getattr(source, 'recent_days', None) or 7,
                'local_staging_directory': getattr(source, 'local_staging_directory', '')
                    or str(settings.BASE_DIR / 'runtime' / 'pc-staging'),
            }
            for field, value in defaults.items():
                if not data.get(field):
                    data[field] = value
            # Missing checkboxes in a submitted HTML form mean unchecked; a minimal
            # API-style submission without the form marker receives sensible defaults.
            if 'simple_source_form' not in data:
                for field in ('recursive', 'ftp_passive', 'ftp_use_tls'):
                    if field not in data:
                        data[field] = getattr(source, field, field == 'ftp_passive')
            for field in ('range_start_date', 'range_end_date', 'domain'):
                if field not in data and source is not None:
                    data[field] = getattr(source, field)
            try:
                if kind == 'smb':
                    path = data.get('shared_path', '').strip().replace('/', '\\').rstrip('\\')
                    match = re.fullmatch(r'\\\\([^\\]+)\\([^\\]+)(?:\\(.*))?', path)
                    if not match or match[1] in ('.', '?') or any(c in match[2] for c in ':*?'):
                        raise ValueError('请输入完整共享路径，例如 \\\\服务器\\共享文件夹\\日志目录。')
                    incoming = normalize_path(match[3] or '')
                    data['host'], data['share_name'] = match[1], match[2]
                    same_path = same_kind and shared_directory(source).casefold() == path.casefold()
                    data['remote_root_directory'] = source.remote_root_directory if same_path else ''
                    data['remote_incoming_directory'] = source.remote_incoming_directory if same_path else incoming or '.'
                    # Keep a deliberately configured script alias. Otherwise track the new path.
                    terminal = data.get('terminal_windows_path', getattr(source, 'terminal_windows_path', ''))
                    old_path = shared_directory(source)
                    if not terminal or terminal.casefold() == old_path.casefold():
                        terminal = '\\\\' + match[1] + '\\' + match[2] + ('\\' + incoming.replace('/', '\\') if incoming else '')
                    data['terminal_windows_path'] = terminal
                elif kind == 'ftp':
                    path = data.get('ftp_directory', '').strip().replace('\\', '/')
                    if not path:
                        raise ValueError('请填写 FTP 日志目录，例如 /logs/incoming；根目录填 /。')
                    incoming = normalize_path(path.lstrip('/'))
                    same_path = same_kind and normalize_path(source_directory(source)) == incoming
                    data['remote_root_directory'] = source.remote_root_directory if same_path else '/'
                    data['remote_incoming_directory'] = source.remote_incoming_directory if same_path else incoming or '.'
                    data['share_name'] = data['domain'] = ''
                else:
                    raise ValueError('请选择共享文件夹或 FTP / FTPS。')
            except ValueError as exc:
                self.path_error = str(exc)
            incoming = normalize_path(data.get('remote_incoming_directory', '.')) if not self.path_error else ''
            for field, leaf in (('remote_processed_directory', '_processed'), ('remote_failed_directory', '_failed')):
                if not data.get(field):
                    data[field] = getattr(source, field, '') or posixpath.join(incoming, leaf)
            for field in ('terminal_windows_path', 'terminal_macos_path'):
                if field not in data:
                    data[field] = getattr(source, field, '')
        super().__init__(data, *args, **kwargs)
        if source is None:
            for name in ('remote_processed_directory', 'remote_failed_directory',
                         'local_staging_directory', 'terminal_windows_path'):
                self.initial[name] = ''
                self.fields[name].widget.attrs['placeholder'] = '留空自动填写'
            self.initial.setdefault('source_type', 'smb')
            self.initial.setdefault('port', 445)
            self.initial.setdefault('file_time_mode', 'recent_days')
            self.initial.setdefault('recent_days', 7)
        self.initial['shared_path'] = shared_directory(source)
        self.initial['ftp_directory'] = '/' + source_directory(source) if source and source.source_type == 'ftp' else '/'
        self.fields['source_type'].choices = [('smb', '共享文件夹（Windows / SMB）'), ('ftp', 'FTP / FTPS')]
        self.fields['username'].help_text = r'有权限读取日志的账号；域账号可填 域名\账号 或 账号@域名。'
        self.fields['host'].help_text = '只填 FTP 服务器 IP 或主机名，不加 ftp://。'
        self.fields['port'].help_text = '通常不用改：共享文件夹 445，FTP/显式 FTPS 21。'
        self.fields['domain'].label = '单独指定域（可不填）'
        self.fields['terminal_windows_path'].help_text = '共享文件夹会自动填写；FTP 获取时，仅下载 Windows 脚本才需要另外填写共享路径。'
        for name in ('port', 'remote_processed_directory', 'remote_failed_directory',
                     'local_staging_directory', 'terminal_windows_path'):
            self.fields[name].required = False
        for name in ('share_name', 'remote_root_directory', 'remote_incoming_directory'):
            self.fields[name].widget = forms.HiddenInput()
        self.fields['ftp_passive'].initial = True
        from index.common.form_examples import apply_field_examples
        from index.devices.parameter_examples import PC_SOURCE_EXAMPLES
        apply_field_examples(self, PC_SOURCE_EXAMPLES)

    def clean(self):
        cleaned = super().clean()
        if self.path_error:
            self.add_error('shared_path' if cleaned.get('source_type') == 'smb' else 'ftp_directory',
                           self.path_error)
        return cleaned
