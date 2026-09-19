"""Bounded API ingestion; all parsing happens before the short DB transaction."""
from dataclasses import dataclass
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.utils import timezone
from net.models import ComputerLogFile, PCUploadConfig
from .logs import MAX_LOG_FILE_BYTES, _read_payload, _computer_defaults, _refresh_static_computer, _isolated_retry
from .checks import parse_local_datetime

@dataclass(frozen=True)
class UploadOutcome:
    log_file: ComputerLogFile
    status: str

def ingest_log(raw, *, require_enabled=True):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_LOG_FILE_BYTES:
        raise ValidationError('日志为空或超过 16 MB。')
    payload, digest, error = _read_payload(raw)
    if error:
        raise ValidationError(error)
    stamp = parse_local_datetime(payload.get('日志时间'))
    if not stamp:
        raise ValidationError('日志时间缺失或格式无效。')
    if timezone.is_naive(stamp):
        stamp = timezone.make_aware(stamp)
    if stamp > timezone.now() + timedelta(minutes=10):
        raise ValidationError('日志时间超过平台当前时间十分钟，请检查终端时钟。')
    platform = payload.get('platform')
    if platform not in ('windows', 'macos'):
        raise ValidationError('platform 必须是 windows 或 macos。')
    name, _, defaults = _computer_defaults(payload, stamp)
    if len(name) > 255 or any(ord(char) < 32 for char in name):
        raise ValidationError('计算机名无效。')
    # Missing optional sections are supported; unexpected container shapes are
    # reported by the existing analysis schema, not changed into empty success.
    from net.models import Computer
    for key, value in defaults.items():
        field = Computer._meta.get_field(key)
        if getattr(field, 'max_length', None) and isinstance(value, str) and len(value) > field.max_length:
            raise ValidationError('日志中的设备基本资料超过字段长度限制。')
    day = timezone.localdate(stamp)
    def persist():
        config = PCUploadConfig.load()
        if require_enabled and (config is None or not config.is_enabled):
            raise ValidationError('PC 日志上报未启用。')
        if config is None:
            config = PCUploadConfig(log_retention='daily_latest')
        computer = _refresh_static_computer(payload, stamp)
        existing = ComputerLogFile.objects.filter(content_hash=digest).first()
        if existing:
            return UploadOutcome(existing, 'duplicate')
        daily = ComputerLogFile.objects.filter(computer=computer, collected_date=day, retained=True)
        if config.log_retention == 'daily_latest':
            newer = daily.filter(collected_at__gte=stamp).order_by('-collected_at', '-pk').first()
            if newer:
                return UploadOutcome(newer, 'older_ignored')
        log = ComputerLogFile.objects.create(
            computer=computer, collected_date=day, collected_at=stamp,
            platform=platform, content_hash=digest, file_size=len(raw), payload=payload,
        )
        if config.log_retention == 'daily_latest':
            daily.exclude(pk=log.pk).update(retained=False)
            from .retention import cleanup_logs_for_computer
            cleanup_logs_for_computer(computer.pk)
        return UploadOutcome(log, 'created')
    return _isolated_retry(persist)
