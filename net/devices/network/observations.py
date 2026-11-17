"""Locate an IP or MAC using ARP/MAC evidence inspections already collect.

Read-only projection over saved results: no extra device access, no new
scanner. ARP and MAC entries are passive observations with a timestamp - they
never prove a host is currently online, and every row carries its source time.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from net.models import Network_Device_Inspection

LOOKBACK_DAYS = 7
MAX_RECORDS = 5000
MAX_MATCHES = 50

_IP_KEYS = ("ip_address", "ip", "address", "ipv4_address")
_MAC_KEYS = ("mac_address", "mac", "mac_addr")
_INTERFACE_KEYS = ("interface", "port", "iface", "physical_interface")
_VLAN_KEYS = ("vlan", "vlan_id", "vlanid")


def _first(record, keys):
    if not isinstance(record, dict):
        return ""
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != "-":
            return text
    return ""


def normalize_mac(value):
    """Compare MACs regardless of separators: aa:bb:cc equals aabbcc."""
    return "".join(char for char in str(value).lower() if char.isalnum())


def _records(details, item):
    payload = details.get(item) if isinstance(details, dict) else None
    if not isinstance(payload, dict):
        return []
    rows = payload.get("records")
    return rows if isinstance(rows, list) else []


def latest_inspections(*, lookback_days=LOOKBACK_DAYS):
    """One successful result per device: the newest inside the lookback window."""
    since = timezone.now() - timedelta(days=lookback_days)
    rows = (Network_Device_Inspection.objects
            .select_related("device", "task_target")
            .filter(status="success", created_at__gte=since)
            .order_by("-created_at")[:MAX_RECORDS])
    latest = {}
    for row in rows:
        latest.setdefault(row.device_id, row)
    return latest


def observed_addresses(*, lookback_days=LOOKBACK_DAYS):
    """Every IP in ARP evidence, with the normalized MACs and device that saw it."""
    latest = latest_inspections(lookback_days=lookback_days)
    seen = {}
    for inspection in latest.values():
        for record in _records(inspection.details or {}, "arp_table"):
            ip = _first(record, _IP_KEYS)
            if not ip:
                continue
            entry = seen.setdefault(ip, {"macs": set(), "devices": []})
            mac = _first(record, _MAC_KEYS)
            if mac:
                entry["macs"].add(normalize_mac(mac))
            entry["devices"].append(inspection.device.device_name)
    return {"addresses": seen, "devices": len(latest)}


def search(query, *, lookback_days=LOOKBACK_DAYS):
    """Match one IP or MAC against each device's latest successful result."""
    needle = (query or "").strip()
    if not needle:
        return {"query": "", "matches": [], "devices": 0, "window_days": lookback_days}
    needle_mac = normalize_mac(needle)
    needle_ip = needle.lower()

    latest = latest_inspections(lookback_days=lookback_days)

    matches = []
    truncated = False
    for inspection in latest.values():
        details = inspection.details or {}
        device = inspection.device
        for source, item in (("arp", "arp_table"), ("mac", "mac_table")):
            for record in _records(details, item):
                ip = _first(record, _IP_KEYS)
                mac = _first(record, _MAC_KEYS)
                hit_ip = bool(ip) and ip.lower() == needle_ip
                hit_mac = bool(mac) and bool(needle_mac) and normalize_mac(mac) == needle_mac
                if not (hit_ip or hit_mac):
                    continue
                if len(matches) >= MAX_MATCHES:
                    truncated = True
                    break
                matches.append({
                    "device_id": str(device.pk),
                    "device_name": device.device_name,
                    "device_ip": device.ip,
                    "ip": ip,
                    "mac": mac,
                    "interface": _first(record, _INTERFACE_KEYS),
                    "vlan": _first(record, _VLAN_KEYS),
                    "source": source,
                    "observed_at": inspection.created_at,
                    "task_id": inspection.task_target.task_id if inspection.task_target_id else None,
                })
            if truncated:
                break
        if truncated:
            break

    return {
        "query": needle,
        "matches": matches,
        "devices": len(latest),
        "window_days": lookback_days,
        "truncated": truncated,
    }
