"""XML parsing that refuses entity expansion.

xml.etree expands internal entities, so a few hundred crafted bytes can expand to
megabytes and stall a Worker thread. The rows/columns/cell limits in the callers all
run *after* parsing, so they cannot help. Rejecting DOCTYPE/ENTITY up front buys that
protection without taking on defusedxml.
"""
from xml.etree import ElementTree

_PROLOGUE = 4096


def safe_fromstring(payload):
    """Parse XML, raising ValueError when it declares a DOCTYPE or ENTITY."""
    head = payload[:_PROLOGUE]
    if isinstance(head, bytes):
        head = head.decode('utf-8', 'replace')
    lowered = head.lower()
    if '<!doctype' in lowered or '<!entity' in lowered:
        raise ValueError('XML with a DOCTYPE or entity declaration is rejected.')
    return ElementTree.fromstring(payload)
