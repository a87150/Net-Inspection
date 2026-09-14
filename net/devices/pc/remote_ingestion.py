"""Durable remote transfer orchestration; remote moves happen only after DB commit."""
from copy import deepcopy
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from queue import SimpleQueue
from threading import local
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import posixpath
import re
import time
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.utils import timezone

from net.models import ComputerLogTransfer
from .connectors.base import PCLogConnectionError, RemoteFileChanged, select_entries
from .connectors.factory import build_connector
from .configuration import source_snapshot, source_from_snapshot, ORIGIN_FIELDS
from .logs import (
    MAX_LOG_FILE_BYTES, LogLeaseLost, _isolated_retry, _reject_links,
    check_log_lease, import_log_bytes,
)


@dataclass
class FetchSummary:
    discovered: int = 0
    downloaded: int = 0
    imported: int = 0
    duplicate: int = 0
    failed: int = 0
    skipped: int = 0
    move_failures: int = 0
    transfers: list = field(default_factory=list)
    log_files: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _staging_root(source):
    root = Path(source.local_staging_directory)
    if not root.is_absolute():
        raise ValidationError('本地暂存目录必须是绝对路径。')
    _reject_links(root)
    root.mkdir(parents=True, exist_ok=True)
    _reject_links(root)
    return root.resolve(strict=True)


def _save_transfer(transfer, target, **values):
    def save():
        check_log_lease(target, lock=True)
        current = ComputerLogTransfer.objects.select_for_update().get(pk=transfer.pk)
        for key, value in values.items():
            setattr(current, key, value)
        current.save(update_fields=[*values, 'updated_at'])
        return current
    return _isolated_retry(save)


def _clean_stale_staging(root, target):
    """Only retire our old UUID temp files, never operator files or live downloads."""
    protected = set(ComputerLogTransfer.objects.filter(
        task_target__task__status__in=['queued', 'running'],
    ).values_list('local_staging_path', flat=True))
    cutoff = time.time() - 86400
    for path in root.iterdir():
        check_log_lease(target)
        if not re.fullmatch(r'[0-9a-f]{32}(?:\.verify)?\.part', path.name):
            continue
        if str(path) in protected or path.is_symlink():
            continue
        try:
            _reject_links(path)
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except (OSError, ValidationError):
            continue


def _archive_name(source, transfer):
    directory = (source.remote_processed_directory if transfer.log_file.import_status == 'imported'
                 else source.remote_failed_directory)
    # Transfer identity keeps same-day duplicate copies distinct, including identical bytes.
    filename = f'{transfer.pk}-{transfer.content_hash[:16]}.json'
    return posixpath.join(directory.replace('\\', '/'), filename)


def _verify_remote(connector, path, transfer, root):
    temporary = root / (uuid4().hex + '.verify.part')
    try:
        metadata = connector.download(path, temporary)
        if metadata.size != transfer.remote_size or temporary.stat().st_size > MAX_LOG_FILE_BYTES:
            raise RemoteFileChanged('远程文件已改变，保留文件等待人工检查或下次获取。')
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != transfer.content_hash:
            raise RemoteFileChanged('远程文件内容已改变，不能归档本次记录。')
    finally:
        temporary.unlink(missing_ok=True)


def _archive_one(source, connector, transfer, root, target):
    check_log_lease(target)
    if not transfer.remote_archive_path:
        transfer = _save_transfer(
            transfer, target, remote_archive_path=_archive_name(source, transfer), stage='archive_pending')
    else:
        transfer = _save_transfer(transfer, target, stage='archive_pending')
    # A previous worker may have moved the file and crashed before persisting completion.
    try:
        connector.stat(transfer.remote_archive_path)
    except PCLogConnectionError:
        original = connector.stat(transfer.remote_source_path)
        try:
            if original.size != transfer.remote_size or original.modified_at != transfer.observed_mtime:
                raise RemoteFileChanged('远程源文件版本已改变，旧归档不能移动新日志。')
            _verify_remote(connector, transfer.remote_source_path, transfer, root)
        except RemoteFileChanged:
            # Release the old active identity, so a newer generation can be discovered.
            _save_transfer(transfer, target, stage='failed',
                           error_message='源文件已被新版本替换；已导入的数据保留，新版本等待获取。')
            raise
        check_log_lease(target)
        connector.move(transfer.remote_source_path, transfer.remote_archive_path)
    _verify_remote(connector, transfer.remote_archive_path, transfer, root)
    transfer = _save_transfer(transfer, target, stage='completed', error_message='')
    return transfer


def recover_remote_archives(source, *, connector=None, task_target=None):
    source = deepcopy(source)
    owned = connector is None
    connector = connector or build_connector(source)
    summary = FetchSummary()
    try:
        root = _staging_root(source)
        pending = ComputerLogTransfer.objects.filter(
            source_id=source.pk, stage__in=['imported', 'archive_pending']
        ).select_related('log_file').order_by('pk')
        for transfer in pending.iterator(chunk_size=100):
            check_log_lease(task_target)
            frozen = transfer.source_snapshot
            if frozen and any(frozen.get(key) != getattr(source, key) for key in ORIGIN_FIELDS):
                summary.skipped += 1
                summary.errors.append(f'传输 {transfer.pk} 属于旧日志服务器，未在当前服务器执行归档。')
                continue
            try:
                archive_source = source_from_snapshot(frozen) if frozen else source
                transfer = _archive_one(archive_source, connector, transfer, root, task_target)
            except LogLeaseLost:
                raise
            except (PCLogConnectionError, OSError, ValidationError, DatabaseError):
                summary.move_failures += 1
                summary.errors.append(f'传输 {transfer.pk} 归档恢复失败，将在下次任务重试。')
                transfer.refresh_from_db()
            summary.transfers.append(transfer)
        return summary
    finally:
        if owned:
            connector.close()


@contextmanager
def _prefetch_downloads(source, entries, root, workers, target):
    workers = max(1, min(64, int(workers)))
    if workers == 1 or len(entries) <= 1:
        yield ((entry, root / (uuid4().hex + '.part'), None) for entry in entries)
        return
    clients, paths = [], []
    pool = None
    try:
        available = SimpleQueue()
        for _ in range(min(workers, len(entries))):
            # Resolve credentials before crossing a thread boundary. Each thread
            # owns one independent protocol connection for this bounded batch.
            client = build_connector(deepcopy(source))
            clients.append(client)
            available.put(client)
        thread = local()
        def download(entry, path):
            if not hasattr(thread, 'client'):
                thread.client = available.get()
            return thread.client.download(entry.path, path)
        pool = ThreadPoolExecutor(max_workers=len(clients), thread_name_prefix='pc-log-download')
        def results():
            pending = deque()
            iterator = iter(entries)
            def submit():
                entry = next(iterator, None)
                if entry is None:
                    return
                check_log_lease(target)
                path = root / (uuid4().hex + '.part')
                paths.append(path)
                pending.append((entry, path, pool.submit(download, entry, path)))
            for _ in clients:
                submit()
            while pending:
                yield pending.popleft()
                submit()
        yield results()
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        for client in clients:
            client.close()
        for path in paths:
            path.unlink(missing_ok=True)


def fetch_remote_logs(source, *, task_target, now=None, connector=None, max_download_workers=1):
    source = deepcopy(source)
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValidationError('获取时间必须包含时区。')
    owned = connector is None
    connector = connector or build_connector(source)
    try:
        root = _staging_root(source)
        check_log_lease(task_target)
        _clean_stale_staging(root, task_target)
        summary = recover_remote_archives(source, connector=connector, task_target=task_target)
        handled = {transfer.remote_source_path for transfer in summary.transfers
                   if transfer.stage in ComputerLogTransfer.ACTIVE_STAGES}
        entries = select_entries(source, connector.list_json(), now=now)
        summary.discovered = len(entries)
        candidates = []
        for entry in entries:
            check_log_lease(task_target)
            if entry.path in handled:
                summary.skipped += 1
                continue
            if len(entry.path) > 512 or entry.size > MAX_LOG_FILE_BYTES:
                summary.skipped += 1
                summary.errors.append('日志路径过长或文件超过大小限制，源文件已保留。')
                continue

            candidates.append(entry)

        workers = max_download_workers if owned else 1
        with _prefetch_downloads(source, candidates, root, workers, task_target) as downloads:
            for entry, temporary, future in downloads:
                def claim():
                    check_log_lease(task_target, lock=True)
                    existing = ComputerLogTransfer.objects.select_for_update().filter(
                        source_id=source.pk, remote_source_path=entry.path, observed_mtime=entry.modified_at,
                        stage__in=ComputerLogTransfer.ACTIVE_STAGES).first()
                    if existing is not None and existing.source_snapshot and any(
                        existing.source_snapshot.get(key) != getattr(source, key) for key in ORIGIN_FIELDS
                    ):
                        existing.stage = 'failed'
                        existing.error_message = '日志服务器配置已更换，保留旧传输记录，不在新服务器恢复旧归档。'
                        existing.save(update_fields=['stage', 'error_message', 'updated_at'])
                        existing = None
                    if existing is None:
                        existing = ComputerLogTransfer.objects.create(
                            source_id=source.pk, task_target=task_target, remote_source_path=entry.path,
                            remote_size=entry.size, observed_mtime=entry.modified_at,
                            source_snapshot=source_snapshot(source))
                    existing.task_target = task_target
                    existing.attempt_count += 1
                    existing.save(update_fields=['task_target', 'attempt_count', 'updated_at'])
                    return existing

                transfer = _isolated_retry(claim)
                try:
                    transfer = _save_transfer(transfer, task_target, local_staging_path=str(temporary))
                    downloaded = (future.result() if future is not None
                                  else connector.download(entry.path, temporary))
                    check_log_lease(task_target)
                    if downloaded != entry or temporary.stat().st_size != entry.size:
                        raise RemoteFileChanged('发现与下载的日志版本不一致，保留源文件。')
                    transfer = _save_transfer(transfer, task_target, stage='downloaded')
                    summary.downloaded += 1
                    transfer.task_target = task_target
                    outcome = import_log_bytes(
                        raw=temporary.read_bytes(), source_path=str(temporary), modified_at=entry.modified_at,
                        source_protocol=('ftps' if source.source_type == 'ftp' and source.ftp_use_tls
                                         else source.source_type),
                        remote_source_path=entry.path, transfer=transfer)
                    transfer.refresh_from_db()
                    if outcome.status == 'imported':
                        summary.imported += 1
                        summary.log_files.append(outcome.log_file)
                    elif outcome.status == 'failed_schema':
                        summary.failed += 1
                    else:
                        summary.duplicate += 1
                    try:
                        transfer = _archive_one(source, connector, transfer, root, task_target)
                    except LogLeaseLost:
                        raise
                    except (PCLogConnectionError, OSError, ValidationError, DatabaseError):
                        summary.move_failures += 1
                        summary.errors.append(f'传输 {transfer.pk} 已入库，但归档失败，将自动重试。')
                except LogLeaseLost:
                    raise
                except (PCLogConnectionError, OSError, ValidationError):
                    summary.skipped += 1
                    summary.errors.append(f'传输 {transfer.pk} 获取失败，远程源文件已保留。')
                    transfer = _save_transfer(
                        transfer, task_target, stage='failed', error_message='获取失败，源文件保留待重试。')
                finally:
                    temporary.unlink(missing_ok=True)
                summary.transfers.append(transfer)
        return summary
    finally:
        if owned:
            connector.close()
