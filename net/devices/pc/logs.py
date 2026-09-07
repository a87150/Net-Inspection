"""Safe server-side import of PowerShell JSON computer logs.

Only paths configured on ``ComputerAnalysisProfile`` are read. A scanner first
commits evidence to the database and only then moves the source file, so an
archive outage cannot make the report disappear.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat as stat_module
import ctypes
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from threading import RLock

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, OperationalError, connections, transaction
from django.utils import timezone

from net.models import Computer, ComputerAnalysisProfile, ComputerLogFile, ComputerLogArchive
from net.devices.pc.snapshot import extract_computer_snapshot
from net.infrastructure.sanitization import sanitize
from net.devices.pc.checks import parse_local_datetime


MAX_LOG_FILE_BYTES = 16 * 1024 * 1024
_SQLITE_IMPORT_LOCK = RLock()


@dataclass
class ScanSummary:
    imported: int = 0
    duplicate: int = 0
    failed: int = 0
    skipped: int = 0
    move_failures: int = 0
    log_files: list[ComputerLogFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _aware_local(value):
    if timezone.is_aware(value):
        return value
    return timezone.make_aware(value, timezone.get_current_timezone())


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_links(path):
    # Windows junctions and linked ancestors are as unsafe as a final symlink.
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise ValidationError({'path': '日志路径包含符号链接或目录联接。'})


def _require_transaction_boundary():
    connection = connections['default']
    if connection.in_atomic_block and not connection.atomic_blocks[-1]._from_testcase:
        raise ValidationError({'database': '日志扫描/导入必须在独立事务边界执行。'})


def _import_database_guard():
    """Serialize local SQLite writes; MySQL coordinates import races in the DB."""
    if connections['default'].vendor == 'sqlite':
        return _SQLITE_IMPORT_LOCK
    return nullcontext()


def _configured_roots(profile, *, create_archives=False):
    if not isinstance(profile, ComputerAnalysisProfile):
        raise ValidationError({'profile': '必须提供计算机日志分析配置。'})
    try:
        profile.full_clean()
    except ValidationError as exc:
        raise ValidationError({'profile': exc.message_dict}) from exc

    roots = []
    for raw_root in profile.scan_directories:
        candidate = Path(raw_root)
        _reject_links(candidate)
        if not candidate.is_absolute() or candidate.is_symlink():
            raise ValidationError({'scan_directories': '扫描目录必须是非符号链接的绝对路径。'})
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValidationError({'scan_directories': f'扫描目录不可用：{candidate}'}) from exc
        if not resolved.is_dir():
            raise ValidationError({'scan_directories': f'扫描目录不是文件夹：{candidate}'})
        roots.append(resolved)
    if not roots:
        raise ValidationError({'scan_directories': '至少需要一个扫描目录。'})

    archive_roots = {}
    for kind, configured, default_name in (
        ('processed', profile.processed_directory, 'processed'),
        ('failed', profile.failed_directory, 'failed'),
    ):
        raw_directory = Path(configured) if configured else roots[0] / default_name
        _reject_links(raw_directory)
        if not raw_directory.is_absolute() or raw_directory.is_symlink():
            raise ValidationError({f'{kind}_directory': '归档目录必须是非符号链接的绝对路径。'})
        resolved = raw_directory.resolve(strict=False)
        if not any(_is_within(resolved, root) for root in roots):
            raise ValidationError({
                f'{kind}_directory': '归档目录必须位于一个配置扫描目录之内。',
            })
        if create_archives:
            try:
                resolved.mkdir(parents=True, exist_ok=True)
                resolved = resolved.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValidationError({f'{kind}_directory': f'无法创建归档目录：{resolved}'}) from exc
            if raw_directory.is_symlink() or not any(_is_within(resolved, root) for root in roots):
                raise ValidationError({f'{kind}_directory': '归档目录越过配置扫描根目录。'})
        archive_roots[kind] = resolved
    return tuple(roots), archive_roots


def _safe_candidate(profile, path):
    roots, _archive_roots = _configured_roots(profile)
    source = Path(path)
    _reject_links(source)
    if source.is_symlink():
        raise ValidationError({'path': '拒绝读取符号链接日志文件。'})
    try:
        candidate = source.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValidationError({'path': '日志文件不存在或无法解析。'}) from exc
    if not candidate.is_file() or candidate.suffix.lower() != '.json':
        raise ValidationError({'path': '只支持可读取的 .json 日志文件。'})
    if not any(_is_within(candidate, root) for root in roots):
        raise ValidationError({'path': '日志文件越过配置扫描根目录。'})
    return candidate


def _file_metadata(path: Path):
    stat = path.stat(follow_symlinks=False)
    if stat.st_size > MAX_LOG_FILE_BYTES:
        raise ValidationError({'path': f'日志文件超过 {MAX_LOG_FILE_BYTES} 字节限制。'})
    return stat, datetime.fromtimestamp(
        stat.st_mtime,
        tz=timezone.get_current_timezone(),
    )


def _identity(stat):
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def _changed():
    return ValidationError({'path': '日志文件正在变化，保留源文件待下次重试。'})


def _read_stable(path, expected=None):
    """Read at most MAX+1 bytes from one regular-file descriptor, never a stream.

    O_NOFOLLOW protects POSIX final components; resolved-root validation and
    lstat/fstat identity comparisons also cover replacement on Windows.
    """
    _reject_links(path)
    before = path.stat(follow_symlinks=False)
    if not stat_module.S_ISREG(before.st_mode):
        raise _changed()
    if expected is not None and _identity(before) != list(expected):
        raise _changed()
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _reject_links(path)
        if not stat_module.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise _changed()
        if opened.st_size > MAX_LOG_FILE_BYTES:
            raise ValidationError({'path': '日志文件超过读取大小限制。'})
        chunks, remaining = [], MAX_LOG_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(descriptor)
        _reject_links(path)
        current = path.stat(follow_symlinks=False)
        if (len(raw) > MAX_LOG_FILE_BYTES or len(raw) != opened.st_size or
                _identity(opened) != _identity(after) or
                _identity(opened) != _identity(current)):
            raise _changed()
        return raw, _identity(opened)
    finally:
        os.close(descriptor)


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
                if isinstance(exc, OperationalError) and exc.args[0] not in (1205, 1213):
                    raise
                if attempt == 2:
                    raise ValidationError({'database': '并发导入冲突，保留源文件待重试。'}) from exc


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
            collected_at = parse_local_datetime(payload.get('日志时间'))
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
                target.scan_logs.add(log)
        return ImportOutcome(log, status)

    return _isolated_retry(persist)


def import_log_file(profile, path, *, scan_target=None) -> ComputerLogFile:
    """Persist one bounded PowerShell JSON file and refresh static inventory.

    The path must resolve inside a configured scan directory. Invalid JSON is
    recorded as a failed ``ComputerLogFile`` rather than discarding evidence.
    """
    _require_transaction_boundary()
    candidate = _safe_candidate(profile, path)
    stat, modified_at = _file_metadata(candidate)
    raw, identity = _read_stable(candidate, _identity(stat))
    payload, digest, parse_error = _read_payload(raw)
    if not parse_error:
        try:
            _computer_defaults(payload, modified_at)
        except ValidationError as exc:
            parse_error = '; '.join(exc.messages)

    def persist():
        if scan_target is not None:
            from net.models import TaskRun
            task = TaskRun.objects.select_for_update().get(pk=scan_target.task_id)
            if (task.status != 'running' or task.worker_id != scan_target.task.worker_id
                    or task.attempt_count != scan_target.task.attempt_count
                    or task.lease_expires_at is None or task.lease_expires_at <= timezone.now()):
                raise ValidationError('扫描租约失效，保留源文件。')
        existing = ComputerLogFile.objects.select_for_update().filter(content_hash=digest).first()
        if existing is not None:
            if scan_target is not None and existing.import_status == 'imported':
                scan_target.scan_logs.add(existing)
            return existing, False
        if not parse_error:
            _refresh_static_computer(payload, modified_at)
        log = ComputerLogFile.objects.create(
            content_hash=digest, source_path=str(candidate),
            modified_at=modified_at, file_size=stat.st_size,
            import_status='failed' if parse_error else 'imported',
            parse_error=sanitize(parse_error)[:4096], payload=payload,
        )
        if scan_target is not None and log.import_status == 'imported':
            scan_target.scan_logs.add(log)
        return log, True

    log_file, created = _isolated_retry(persist)
    log_file._import_outcome = log_file.import_status if created else 'duplicate'
    log_file._archive_kind = 'processed' if log_file.import_status == 'imported' else 'failed'
    log_file._source_identity = identity
    return log_file


def _within_time_range(profile, modified_at, now):
    if profile.file_time_mode == ComputerAnalysisProfile.FileTimeMode.RECENT_DAYS:
        return now - timedelta(days=profile.recent_days) <= modified_at <= now
    if profile.file_time_mode == ComputerAnalysisProfile.FileTimeMode.DATE_RANGE:
        local_timezone = timezone.get_current_timezone()
        start = timezone.make_aware(
            datetime.combine(profile.range_start_date, time.min), local_timezone,
        )
        end_exclusive = timezone.make_aware(
            datetime.combine(profile.range_end_date + timedelta(days=1), time.min),
            local_timezone,
        )
        # User-entered dates include both complete local calendar days.
        return start <= modified_at < end_exclusive
    raise ValidationError({'file_time_mode': '不支持的文件时间范围模式。'})


def _pending_archive(log_file, source, archive_root):
    identity = log_file._source_identity
    key = hashlib.sha256(json.dumps(
        [str(source), log_file.content_hash, identity], ensure_ascii=True,
    ).encode()).hexdigest()

    def persist():
        existing = ComputerLogArchive.objects.select_for_update().filter(pk=key).first()
        return existing or ComputerLogArchive.objects.create(
            id=key, log_file=log_file, source_path=str(source), identity=identity,
            destination_path=str(archive_root / f'{key}.json'),
        )

    return _isolated_retry(persist)


def _rename_noreplace(source, destination):
    """Atomic no-replace rename, with no copy/delete or overwriting fallback.

    Cross-filesystem and unsupported filesystems fail safely, leaving source.
    Windows rename refuses an existing destination; Linux needs renameat2.
    """
    if os.name == 'nt':
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('Filesystem does not support atomic no-replace rename')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(source))


def _archive_destination(profile, destination):
    _roots, archives = _configured_roots(profile)
    destination = Path(destination)
    if (destination.is_symlink() or
            not any(destination.parent == root for root in archives.values()) or
            destination.parent.resolve(strict=True) != destination.parent):
        raise ValidationError({'path': '归档目标越过配置目录或为符号链接。'})
    return destination


def _verify_evidence(path, identity, digest):
    raw, _identity_read = _read_stable(path, identity)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise _changed()


def _restore_changed(profile, source, destination):
    """Put a changed moved file back into the scan area without losing a new upload."""
    source = Path(source)
    roots, _archives = _configured_roots(profile)
    parent = source.parent.resolve(strict=True)
    if parent != source.parent or not any(_is_within(parent, root) for root in roots):
        raise ValidationError({'path': '恢复路径越过配置扫描目录。'})
    try:
        _rename_noreplace(destination, source)
    except FileExistsError:
        # A producer already published the next generation at the original
        # name. Preserve both; this deterministic sibling is scanned next poll.
        retry = parent / f'{destination.stem}-retry.json'
        _rename_noreplace(destination, retry)


def _move_safely(profile, source, destination, digest, identity):
    candidate = _safe_candidate(profile, source)
    destination = _archive_destination(profile, destination)
    _verify_evidence(candidate, identity, digest)
    _rename_noreplace(candidate, destination)
    try:
        # Also catch a producer changing bytes in the check/rename window.
        _verify_evidence(destination, identity, digest)
    except (OSError, ValidationError):
        # Never overwrite a newly produced source. If restoration cannot run,
        # the durable pending path still locates the changed file for recovery.
        _restore_changed(profile, candidate, destination)
        raise
    return destination


def _complete_archive(archive):
    def persist():
        locked = ComputerLogArchive.objects.select_for_update().get(pk=archive.pk)
        log = ComputerLogFile.objects.select_for_update().get(pk=locked.log_file_id)
        if not log.archived_path:
            log.archived_path = locked.destination_path
            log.save(update_fields=['archived_path'])
        locked.status = 'completed'
        locked.save(update_fields=['status'])
    _isolated_retry(persist)


def _archive_one(profile, archive):
    destination = _archive_destination(profile, archive.destination_path)
    if destination.exists():
        # A pending destination may exist after a process crash or DB failure.
        # Only the recorded identity AND bytes authorize marking it complete.
        try:
            _verify_evidence(destination, archive.identity, archive.log_file.content_hash)
        except ValidationError:
            if _identity(destination.stat(follow_symlinks=False))[:2] == archive.identity[:2]:
                _restore_changed(profile, archive.source_path, destination)
            raise
    else:
        _move_safely(profile, archive.source_path, destination,
                     archive.log_file.content_hash, archive.identity)
    _complete_archive(archive)


def _reconcile_archives(profile, roots, summary):
    handled = set()
    pending = ComputerLogArchive.objects.filter(status='pending').select_related('log_file')
    for archive in pending.iterator(chunk_size=100):
        source = Path(archive.source_path)
        if not any(_is_within(source, root) for root in roots):
            continue
        try:
            _archive_one(profile, archive)
        except (OSError, ValidationError, DatabaseError) as exc:
            summary.move_failures += 1
            summary.errors.append(sanitize(f'归档恢复失败：{source.name}：{exc}')[:4096])
            # A new source generation must still be scanned, never suppressed by
            # an older pending attempt. Other failures retry on the next poll.
            try:
                candidate = _safe_candidate(profile, source)
                try:
                    _verify_evidence(candidate, archive.identity, archive.log_file.content_hash)
                except ValidationError:
                    if not Path(archive.destination_path).exists():
                        ComputerLogArchive.objects.filter(pk=archive.pk).update(status='changed')
                else:
                    handled.add(source)
            except (OSError, ValidationError, DatabaseError):
                pass
    return handled


def _candidate_paths(roots, archive_roots, recursive):
    archive_values = tuple(archive_roots.values())
    for root in roots:
        iterator = root.rglob('*') if recursive else root.iterdir()
        for item in iterator:
            if item.is_symlink() or not item.is_file() or item.suffix.lower() != '.json':
                continue
            try:
                _reject_links(item)
                resolved = item.resolve(strict=True)
            except (OSError, RuntimeError, ValidationError):
                continue
            if any(_is_within(resolved, archive_root) for archive_root in archive_values):
                continue
            yield resolved


def scan_log_directory(profile, now=None, *, scan_target=None) -> ScanSummary:
    """Scan local roots using timezone-aware mtime selection and safe archiving.

    ``recent_days`` is a rolling inclusive window ending at ``now``. Explicit
    start/end dates cover complete inclusive user calendar days in Django's
    configured timezone. Archive folders beneath scan roots are never rescanned.
    """
    _require_transaction_boundary()
    profile = deepcopy(profile)  # Freeze path/time/recursion settings for this poll.
    now = timezone.now() if now is None else now
    if not isinstance(now, datetime) or timezone.is_naive(now):
        raise ValidationError({'now': '扫描时间必须使用带时区的时间。'})
    roots, archive_roots = _configured_roots(profile, create_archives=True)
    summary = ScanSummary()
    handled = _reconcile_archives(profile, roots, summary)
    for candidate in sorted(_candidate_paths(roots, archive_roots, profile.recursive)):
        if candidate in handled:
            continue
        try:
            _stat, modified_at = _file_metadata(candidate)
            if not _within_time_range(profile, modified_at, now):
                summary.skipped += 1
                continue
            log_file = import_log_file(profile, candidate, scan_target=scan_target)
            outcome = getattr(log_file, '_import_outcome', 'duplicate')
            if outcome == 'imported':
                summary.imported += 1
            elif outcome == 'failed':
                summary.failed += 1
            else:
                summary.duplicate += 1
            summary.log_files.append(log_file)
            archive_root = archive_roots[getattr(log_file, '_archive_kind', 'failed')]
            try:
                archive = _pending_archive(log_file, candidate, archive_root)
                _archive_one(profile, archive)
            except (OSError, ValidationError, DatabaseError) as exc:
                summary.move_failures += 1
                summary.errors.append(sanitize(f'归档失败：{candidate.name}：{exc}')[:4096])
        except (OSError, ValidationError, DatabaseError) as exc:
            summary.failed += 1
            summary.errors.append(sanitize(f'导入失败：{candidate.name}：{exc}')[:4096])
    return summary
