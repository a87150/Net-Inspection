"""Process-local cap on simultaneous device CLI sessions.

A profile's ``concurrent_workers`` bounds one task only. Two inspection tasks
running together — or one profile set to a large value — can exhaust every VTY
line on the devices being polled, and the symptom is mass authentication
failures rather than an obvious concurrency problem.

ponytail: the semaphore is process-local, so N worker processes allow N times
the configured budget. That ceiling is deliberate; a Redis counter is the
upgrade path once multi-process deployments need a true global limit.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from django.conf import settings

DEFAULT_CONCURRENCY = 10
DEFAULT_WAIT_SECONDS = 300
# Weak-current devices reach SNMP/HTTP but never open an interactive CLI session.
CLI_TARGET_TYPES = frozenset({'network_device', 'server'})

_lock = threading.Lock()
_semaphore = None


class BudgetTimeout(RuntimeError):
    """A target waited too long for a free CLI session slot."""


def limit():
    """Configured session cap, ignoring unusable values rather than crashing."""
    raw = getattr(settings, 'NET_SSH_CONCURRENCY', DEFAULT_CONCURRENCY)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CONCURRENCY
    return max(1, value)


def reset():
    """Drop the cached semaphore so a changed setting takes effect (tests)."""
    global _semaphore
    with _lock:
        _semaphore = None


def _current():
    global _semaphore
    if _semaphore is None:
        with _lock:
            if _semaphore is None:
                _semaphore = threading.BoundedSemaphore(limit())
    return _semaphore


@contextmanager
def cli_budget(*, timeout=DEFAULT_WAIT_SECONDS):
    """Hold one session slot for the duration of a target execution."""
    semaphore = _current()
    if not semaphore.acquire(timeout=timeout):
        raise BudgetTimeout(
            f'等待设备命令行并发配额超过 {timeout:g} 秒（上限 {limit()}）。'
            '可调低巡检并发或提高 NET_SSH_CONCURRENCY。'
        )
    try:
        yield
    finally:
        semaphore.release()
