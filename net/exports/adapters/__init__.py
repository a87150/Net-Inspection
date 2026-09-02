"""Pure snapshot adapters. No adapter opens a device connection."""

NOTICE = '只读脱敏配置快照，非完整恢复备份 / NOT A FULL RECOVERY BACKUP'
MAX_CONFIG_BYTES = 4 * 1024 * 1024


class UnsupportedConfiguration(ValueError):
    pass


def readable_text(value):
    return (isinstance(value, str) and bool(value.strip())
            and len(value.encode('utf-8')) <= MAX_CONFIG_BYTES
            and not any(ord(char) < 32 and char not in '\r\n\t' for char in value)
            and '\ufffd' not in value)
