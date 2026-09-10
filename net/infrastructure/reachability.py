"""Bounded, argument-safe ICMP reachability probes."""

from ipaddress import ip_address
import platform
import re
import subprocess


_HOSTNAME = re.compile(r'(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z')


def _target(value):
    value = str(value or '').strip()
    try:
        return str(ip_address(value))
    except ValueError:
        if _HOSTNAME.fullmatch(value):
            return value
    raise ValueError('invalid IP address or hostname')


def ping_host(host, timeout=12):
    """Return ``(reachable, diagnostic)`` without invoking a shell."""
    target = _target(host)
    seconds = max(1, min(int(timeout), 120))
    command = (['ping', '-n', '1', '-w', str(seconds * 1000), target]
               if platform.system() == 'Windows' else ['ping', '-c', '1', '-W', str(seconds), target])
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=seconds + 2, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, 'ICMP probe timed out'
    except OSError:
        return False, 'ICMP probe could not be started'
    return completed.returncode == 0, 'ICMP reply' if completed.returncode == 0 else 'ICMP timed out or was blocked'


__all__ = ['ping_host']
