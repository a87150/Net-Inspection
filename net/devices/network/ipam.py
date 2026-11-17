"""Compare the written IP plan against what the inspections observed."""
from __future__ import annotations

import ipaddress

from net.devices.network.observations import normalize_mac, observed_addresses
from net.models import IpSubnet

SEVERITY_LABELS = {"error": "需处理", "warning": "需确认"}


def _overlapping(subnets):
    issues = []
    for index, left in enumerate(subnets):
        for right in subnets[index + 1:]:
            if left.network.overlaps(right.network):
                issues.append({
                    "kind": "overlap",
                    "severity": "error",
                    "title": "网段重叠",
                    "detail": "%s 与 %s 的地址范围互相包含，按网段统计会重复计数。" % (left.cidr, right.cidr),
                })
    return issues


def plan_report(*, lookback_days=7):
    """Everything the IP page shows, computed in one pass over the evidence."""
    subnets = list(IpSubnet.objects.prefetch_related("addresses"))
    observed = observed_addresses(lookback_days=lookback_days)
    addresses = observed["addresses"]

    issues = _overlapping(subnets)

    for ip, entry in sorted(addresses.items()):
        macs = entry["macs"]
        if len(macs) > 1:
            issues.append({
                "kind": "conflict",
                "severity": "error",
                "title": "同一 IP 出现多个 MAC",
                "detail": "%s 在 %s 上被看到对应 %d 个 MAC：%s。" % (
                    ip, "、".join(sorted(set(entry["devices"]))), len(macs), "、".join(sorted(macs))),
            })

    static_in_pool = []
    for subnet in subnets:
        for address in subnet.addresses.all():
            if address.status in {"used", "reserved"} and subnet.in_pool(address.address):
                static_in_pool.append(address)
    for address in static_in_pool[:50]:
        issues.append({
            "kind": "pool",
            "severity": "warning",
            "title": "静态地址落在 DHCP 池内",
            "detail": "%s 登记为%s，但它在 %s 的地址池里，DHCP 可能把它发给别人。" % (
                address.address, address.get_status_display(), address.subnet.cidr),
        })

    registered = {}
    for subnet in subnets:
        for address in subnet.addresses.all():
            registered[address.address] = address
    for ip, address in registered.items():
        entry = addresses.get(ip)
        if not entry or not address.mac or not entry["macs"]:
            continue
        if normalize_mac(address.mac) not in entry["macs"]:
            issues.append({
                "kind": "drift",
                "severity": "warning",
                "title": "登记 MAC 与观察到的不一致",
                "detail": "%s 登记为 %s，近期巡检看到的是 %s。" % (
                    ip, address.mac, "、".join(sorted(entry["macs"]))),
            })

    managed = {ip for ip in registered}
    unknown = [ip for ip in addresses if ip not in managed]

    return {
        "subnets": subnets,
        "issues": issues,
        "error_count": sum(1 for issue in issues if issue["severity"] == "error"),
        "warning_count": sum(1 for issue in issues if issue["severity"] == "warning"),
        "observed_count": len(addresses),
        "registered_count": len(registered),
        "unplanned_count": len(unknown),
        "unplanned": sorted(unknown)[:20],
        "devices": observed["devices"],
        "window_days": lookback_days,
    }


def classify(address, subnet):
    """Which subnet an address belongs to, so a form can fill it in."""
    try:
        parsed = ipaddress.ip_address(str(address))
    except ValueError:
        return None
    return subnet if parsed in subnet.network else None
