"""Known network CLI families; export only complete saved native text."""
import re

from net.data_exchange.adapters import UnsupportedConfiguration, readable_text


# Scope is the displayed active configuration, not startup/files/certificates.
NETWORK_CONFIG = {
    'cisco': ('terminal length 0', 'show running-config', 'end', 'running-config'),
    'h3c': ('screen-length disable', 'display current-configuration', 'return', 'current-configuration'),
}
VENDOR_ALIASES = {'cisco': ('cisco', '思科'), 'h3c': ('h3c', '华三')}
BAD_OUTPUT = re.compile(
    r'(?im)--\s*more\s*--|----\s*more\s*----|press\s+(?:any|enter|space)|'
    r'\b(?:truncated|pagination)\b|^\s*(?:%\s*)?(?:error\b|invalid\s+(?:input|command)|'
    r'unrecognized\s+command|unknown\s+command|incomplete\s+command|ambiguous\s+command|'
    r'permission\s+denied|access\s+denied|authorization\s+failed|not\s+authorized)|'
    r'^\s*(?:错误|无效命令|命令不完整|权限不足)')


def network_vendor(value):
    value = str(value or '').strip().casefold()
    return next((key for key, aliases in VENDOR_ALIASES.items()
                 if any(re.search(r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])', value)
                        for alias in aliases)), '')


def validate_native(content, vendor):
    if vendor not in NETWORK_CONFIG:
        raise UnsupportedConfiguration('unregistered CLI family')
    if not readable_text(content) or BAD_OUTPUT.search(content):
        raise ValueError('invalid or incomplete native text')
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    terminator = NETWORK_CONFIG[vendor][2]
    meaningful = [line for line in lines[:-1]
                  if line not in ('!', '#') and not line.startswith(('Building configuration', 'Current configuration'))]
    if len(lines) < 2 or lines[-1] != terminator or not meaningful:
        raise ValueError('missing native configuration terminator/body')
    return content


def adapt(item):
    vendor = network_vendor(item.get('vendor'))
    if vendor not in NETWORK_CONFIG or item.get('format') != 'text':
        raise UnsupportedConfiguration('unsupported configuration format/vendor')
    if item.get('complete') is not True or item.get('scope') != NETWORK_CONFIG[vendor][3]:
        raise ValueError('incomplete configuration scope')
    return validate_native(item.get('content'), vendor), 'text/plain', 'txt', item['scope']
