"""Validation, storage and demonstration download for PC software policies."""

import configparser
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse


MAX_SOFTWARE_POLICY_BYTES = 1024 * 1024
_POLICY_SECTIONS = {'WHITELIST', 'SPECIAL_WHITELIST', 'BLACKLIST'}


def _read_uploaded_policy(uploaded_file):
    if getattr(uploaded_file, 'size', 0) > MAX_SOFTWARE_POLICY_BYTES:
        raise ValidationError('软件策略文件不能超过 1 MB。')
    try:
        raw = uploaded_file.read(MAX_SOFTWARE_POLICY_BYTES + 1)
    finally:
        uploaded_file.seek(0)
    if len(raw) > MAX_SOFTWARE_POLICY_BYTES:
        raise ValidationError('软件策略文件不能超过 1 MB。')
    try:
        return raw, raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValidationError('软件策略文件必须使用 UTF-8 编码。') from exc


def validate_software_policy_upload(uploaded_file):
    if Path(uploaded_file.name).suffix.lower() != '.ini':
        raise ValidationError('软件策略文件必须是 .ini 文件。')
    _raw, text = _read_uploaded_policy(uploaded_file)
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ValidationError('软件策略文件格式无效，请使用下载的演示模板。') from exc
    if not (_POLICY_SECTIONS & set(parser.sections())):
        raise ValidationError('软件策略文件至少需要包含白名单、特殊白名单或黑名单配置。')


def software_policy_target(profile_id):
    directory = Path(getattr(
        settings,
        'PC_SOFTWARE_POLICY_DIR',
        Path(settings.BASE_DIR) / 'runtime' / 'software-policies',
    ))
    return directory / f'{profile_id}.ini'


def store_software_policy_upload(uploaded_file, target):
    raw, _text = _read_uploaded_policy(uploaded_file)
    target = Path(target)
    temporary_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='wb', dir=target.parent, prefix='.policy-', suffix='.tmp', delete=False,
        ) as temporary:
            temporary.write(raw)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ValidationError('软件策略文件保存失败，请检查服务器运行目录权限。') from exc


@login_required
def pc_software_policy_template_download(request):
    template_path = Path(settings.BASE_DIR) / 'config' / 'examples' / 'software-policy-demo.ini'
    return FileResponse(
        template_path.open('rb'),
        as_attachment=True,
        filename='software-policy-demo.ini',
        content_type='text/plain; charset=utf-8',
    )
