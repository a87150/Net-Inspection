"""Bounded immutable JSON import; protocol I/O lives in remote_ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
import time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connections, transaction
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

def _reject_links(path):
    # Windows junctions and linked ancestors are as unsafe as a final symlink.
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise ValidationError({'path': '日志路径包含符号链接或目录联接。'})


def _require_transaction_boundary():
    connection = connections['default']
    if connection.in_atomic_block and not connection.atomic_blocks[-1]._from_testcase:
        raise ValidationError({'database': '日志获取/导入必须在独立事务边界执行。'})


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


@dataclass(frozen=True)
class ImportOutcome:
    log_file: ComputerLogFile
    status: str


class LogLeaseLost(ValidationError):
    """A cancelled/reclaimed task must not persist or archive another file."""


def check_log_lease(target, *, lock=False):
    if target is None:
        return
    from net.models import TaskRun
    query = TaskRun.objects
    if lock:
        query = query.select_for_update()
    current = query.get(pk=target.task_id)
    expected = target.task
    if (current.status != 'running' or current.worker_id != expected.worker_id
            or current.attempt_count != expected.attempt_count
            or current.lease_expires_at is None or current.lease_expires_at <= timezone.now()):
        raise LogLeaseLost('任务已停止或租约失效，保留远程源文件。')


def import_log_bytes(*, raw, source_path, modified_at, source_protocol,
                     remote_source_path, transfer):
    """Import immutable remote evidence with one formal log per PC/local day."""
    _require_transaction_boundary()
    if not isinstance(raw, bytes) or len(raw) > MAX_LOG_FILE_BYTES:
        raise ValidationError('日志文件超过读取大小限制。')
    if timezone.is_naive(modified_at):
        raise ValidationError('文件修改时间必须包含时区。')
    if source_protocol not in ('smb', 'ftp', 'ftps'):
        raise ValidationError('远程日志协议无效。')
    payload, digest, error = _read_payload(raw)
    collected_at = None
    platform = ''
    if not error:
        try:
            # Original TerminalLogs.ps1 has no top-level timestamp. The legacy
            # analyzer uses file mtime; preserve the original payload as evidence.
            collected_at = (modified_at if '日志时间' not in payload
                            else parse_local_datetime(payload.get('日志时间')))
            if collected_at is None:
                raise ValidationError('日志时间缺失或格式无效。')
            collected_at = _aware_local(collected_at)
            name, _, defaults = _computer_defaults(payload, modified_at)
            if len(name) > Computer._meta.get_field('computer_name').max_length:
                raise ValidationError('计算机名超过允许长度。')
            os_name = str(defaults.get('os', '')).lower()
            platform = str(payload.get('平台') or payload.get('platform') or
                           ('macos' if 'mac' in os_name or 'darwin' in os_name else 'windows')).lower()
            if platform not in ('windows', 'macos'):
                raise ValidationError('日志平台只支持 Windows 和 macOS。')
        except (ValidationError, ValueError, TypeError, OverflowError) as exc:
            error = '; '.join(exc.messages) if isinstance(exc, ValidationError) else '日志时间或设备信息无效。'

    def persist():
        target = transfer.task_target if transfer is not None else None
        check_log_lease(target, lock=True)
        log = ComputerLogFile.objects.select_for_update().filter(content_hash=digest).first()
        status = 'duplicate_content'
        if log is None:
            computer = None
            collected_date = timezone.localdate(collected_at) if collected_at is not None else None
            if not error:
                computer = Computer.objects.select_for_update().filter(computer_name=name).first()
                if computer is not None:
                    log = ComputerLogFile.objects.filter(
                        computer=computer, collected_date=collected_date, import_status='imported').first()
                if log is not None:
                    status = 'duplicate_day'
                else:
                    computer = _refresh_static_computer(payload, modified_at)
            if log is None:
                status = 'failed_schema' if error else 'imported'
                log = ComputerLogFile.objects.create(
                    computer=computer, collected_date=collected_date if not error else None,
                    platform=platform, source_protocol=source_protocol, remote_source_path=remote_source_path,
                    source_path=source_path, modified_at=modified_at, content_hash=digest,
                    file_size=len(raw), import_status='failed' if error else 'imported',
                    parse_error=sanitize(error)[:4096], payload=payload)
        if transfer is not None:
            from net.models import ComputerLogTransfer
            locked = ComputerLogTransfer.objects.select_for_update().get(pk=transfer.pk)
            locked.log_file = log
            locked.content_hash = digest
            locked.stage = 'imported'
            locked.save(update_fields=['log_file', 'content_hash', 'stage', 'updated_at'])
            if target is not None and status == 'imported':
                target.fetched_logs.add(log)
        return ImportOutcome(log, status)

    return _isolated_retry(persist)
