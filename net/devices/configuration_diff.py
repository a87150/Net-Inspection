"""Line-level comparison between two stored configuration versions.

The diff view is restricted to the same permission as the raw download, so
lines are shown as they are - masking "enable secret" rows would hide exactly
the edit an operator is looking for. Access control lives in the view.
"""
from __future__ import annotations

import difflib

from net.devices.configuration_backups import read_configuration_backup

# A full chassis config stays well under this; the cap protects the page only.
MAX_ROWS = 20000


def decode(raw: bytes) -> str:
    """Configuration bytes as normalized text (CRLF folded to LF)."""
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def text_of(backup) -> str:
    """Decrypted configuration as normalized text."""
    return decode(read_configuration_backup(backup))


def diff_texts(left_text, right_text) -> dict:
    """Compare two already-decoded configurations; left is the older one."""
    left_lines = left_text.split("\n")
    right_lines = right_text.split("\n")
    # autojunk must stay off: configurations repeat short lines often enough
    # that difflib's popularity heuristic would misalign whole blocks.
    matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)
    rows = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                rows.append({
                    "kind": "same",
                    "left": i1 + offset + 1,
                    "right": j1 + offset + 1,
                    "text": left_lines[i1 + offset],
                })
            continue
        for index in range(i1, i2):
            rows.append({
                "kind": "remove", "left": index + 1, "right": None,
                "text": left_lines[index],
            })
            removed += 1
        for index in range(j1, j2):
            rows.append({
                "kind": "add", "left": None, "right": index + 1,
                "text": right_lines[index],
            })
            added += 1
    return {
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "added": added,
        "removed": removed,
        "changed": bool(added or removed),
    }


def diff_backups(left, right) -> dict:
    """Compare two stored versions; left is the older one by convention."""
    return diff_texts(text_of(left), text_of(right))
