"""Shared JSON parsing, computer snapshots, and isolated DB retry helpers."""

from __future__ import annotations

import hashlib
import json
import time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from net.models import Computer, ComputerLogFile
from net.devices.pc.snapshot import extract_computer_snapshot
from net.infrastructure.sanitization import sanitize
from net.devices.pc.checks import parse_local_datetime


MAX_LOG_FILE_BYTES = 16 * 1024 * 1024

def _aware_local(value):
    if timezone.is_aware(value):
        return value
    return timezone.make_aware(value, timezone.get_current_timezone())

def _import_database_guard():
    """Serialize local SQLite writes; MySQL coordinates import races in the DB."""
    # Resolve lazily: executor imports queue helpers that also use PC logs.
    from net.inspections.executor import _database_guard
    return _database_guard()

def _read_payload(raw):
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(payload, dict):
            raise ValueError('PowerShell 日志根节点必须是 JSON 对象。')
        # JSONField must receive finite standard JSON, never NaN/Infinity.
        json.dumps(payload, allow_nan=False)
        payload = sanitize(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        return None, digest, f'JSON 解析失败：{getattr(exc, "msg", str(exc))}'
    return payload, digest, ''


def _computer_defaults(payload, modified_at):
    system_info = payload.get('系统信息概览')
    if not isinstance(system_info, dict):
        raise ValidationError({'path': '日志缺少“系统信息概览”对象。'})
    computer_name = str(system_info.get('计算机名') or '').strip()
    if not computer_name:
        raise ValidationError({'path': '日志缺少计算机名。'})
    collected_at = parse_local_datetime(payload.get('日志时间')) or modified_at
    collected_at = _aware_local(collected_at)
    snapshot = extract_computer_snapshot(
        system_info,
        payload.get('网络信息'),
        payload.get('计算机硬件资源情况'),
        disk_payload=payload,
    )
    return computer_name, collected_at, {
        'is_active': True,
        'os': system_info.get('系统版本类型') or system_info.get('系统主要版本名') or '',
        'user_name': system_info.get('当前登录用户姓名') or '',
        'last_report_at': collected_at,
        **snapshot,
    }


def _refresh_static_computer(payload, modified_at):
    computer_name, collected_at, defaults = _computer_defaults(payload, modified_at)
    computer, created = Computer.objects.select_for_update().get_or_create(
        computer_name=computer_name,
        defaults=defaults,
    )
    if not created and (
        computer.last_report_at is None or collected_at >= computer.last_report_at
    ):
        changed = [
            field for field, value in defaults.items()
            if getattr(computer, field) != value
        ]
        if changed:
            for field in changed:
                setattr(computer, field, defaults[field])
            computer.save(update_fields=changed)
    return computer


def _isolated_retry(operation):
    """A conflict rolls back the WHOLE transaction before any retry lookup.

    durable=True refuses callers with an outer transaction (except Django's
    TestCase wrapper). This prevents an InnoDB REPEATABLE READ stale snapshot.
    Both valid and failed evidence take this identical path.
    """
    with _import_database_guard():
        for attempt in range(3):
            try:
                with transaction.atomic(durable=True):
                    return operation()
            except (IntegrityError, OperationalError) as exc:
                from net.inspections.queue import is_sqlite_busy
                sqlite_busy = is_sqlite_busy(exc)
                if isinstance(exc, OperationalError) and not sqlite_busy and (not exc.args or exc.args[0] not in (1205, 1213)):
                    raise
                if attempt == 2:
                    if sqlite_busy:
                        raise ValidationError({'database': 'SQLite 数据库持续被占用，源文件保留，请稍后重新获取。'}) from exc
                    raise ValidationError({'database': '并发导入冲突，保留源文件待重试。'}) from exc
                if sqlite_busy:
                    time.sleep(0.1 * (attempt + 1))
