"""Status -> badge lookups shared by templates and REST payloads.

Both sides read this map, so a new status cannot get a styled badge on one
surface and a grey one on the other.
"""
STATUS_TONES = (
    ("success", ("success", "normal", "recovery", "sent", "delivered", "enabled", "completed")),
    ("danger", ("failed", "abnormal", "error", "critical")),
    ("warning", ("partial", "warning", "retry", "blocked")),
    ("secondary", ("cancelled", "disabled", "skipped", "unknown")),
)
DEFAULT_TONE = "info"

SEVERITY_BADGES = {
    "critical": {"class": "text-bg-danger", "status": "critical", "label": "严重"},
    "warning": {"class": "text-bg-warning", "status": "warning", "label": "警告"},
    "info": {"class": "text-bg-info", "status": "info", "label": "提示"},
    "normal": {"class": "text-bg-success", "status": "normal", "label": "正常"},
    "success": {"class": "text-bg-success", "status": "success", "label": "正常"},
}
UNKNOWN_SEVERITY = {"class": "text-bg-secondary", "status": "unknown", "label": "未知"}


def status_tone(status):
    """Bootstrap background class for a status value."""
    value = status or "unknown"
    for tone, members in STATUS_TONES:
        if value in members:
            return f"text-bg-{tone}"
    return f"text-bg-{DEFAULT_TONE}"


def severity_badge(level):
    return SEVERITY_BADGES.get(level, UNKNOWN_SEVERITY)


def status_badge(status, label=None):
    """Payload form of the status badge: class + data-status + label."""
    value = status or "unknown"
    return {
        "class": status_tone(value),
        "status": value,
        "label": label or value,
    }
