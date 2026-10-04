"""Guard for tests that shell out to Windows PowerShell.

These tests write a throwaway .ps1 and execute it. That fails for reasons that have
nothing to do with the code under test: the host may not be Windows, may not ship
PowerShell, or may enforce an execution policy (AllSigned/Restricted) that refuses
unsigned scripts. Probing the real condition keeps the skip honest instead of
guessing from os.name alone, which is why the full suite previously could not run
outside a permissive Windows box.
"""
import functools
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

_PROBE = "Write-Output ok"


@functools.lru_cache(maxsize=1)
def powershell_runs_unsigned_scripts():
    """True when this host can execute the throwaway script the tests write.

    Probes inside tempfile.gettempdir() because that is where the tests put their
    script, so a host that only blocks some directories is judged accurately.
    """
    if os.name != 'nt' or shutil.which('powershell') is None:
        return False
    try:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'probe.ps1'
            script.write_text(_PROBE, encoding='utf-8-sig')
            result = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-File', str(script)],
                capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and b'ok' in result.stdout


def _reason():
    if os.name != 'nt':
        return 'Windows PowerShell required (host is not Windows)'
    if shutil.which('powershell') is None:
        return 'Windows PowerShell required (powershell.exe not on PATH)'
    return 'Windows PowerShell refused the unsigned test script (execution policy)'


requires_powershell = unittest.skipUnless(
    powershell_runs_unsigned_scripts(), _reason())
