"""Frozen, database-only enrichment from synchronized personnel and directory rows."""

from collections.abc import Mapping
from ipaddress import ip_address, ip_network
import re

from django.conf import settings
from django.db.models import Q

from net.models import Computer, Domain_Account, Domain_Computer, People


def normalize_login(value):
    if not isinstance(value, str):
        return ''
    return value.strip().rsplit('\\', 1)[-1].split('@', 1)[0].strip()


def _match(queryset, present=True):
    if not present:
        return None, 'missing'
    rows = list(queryset[:2])
    if len(rows) == 1:
        return rows[0], 'matched'
    return None, 'ambiguous' if rows else 'unmatched'


def _ou(row):
    if row is None:
        return ''
    if row.ou:
        return row.ou
    # Split only on unescaped DN separators (CN may contain an escaped comma).
    parts = re.split(r'(?<!\\),', row.distinguished_name or '')
    return ','.join(part.strip() for part in parts if part.strip().upper().startswith('OU='))


def _sites(payload, prefixes):
    networks = []
    for prefix, site in prefixes.items():
        if not isinstance(prefix, str) or not isinstance(site, str) or not site.strip():
            continue
        try:
            if '/' not in prefix and ':' not in prefix:
                octets = prefix.rstrip('.').split('.')
                if not 1 <= len(octets) <= 4:
                    continue
                prefix = '.'.join(octets + ['0'] * (4 - len(octets))) + f'/{8 * len(octets)}'
            networks.append((ip_network(prefix, strict=False), site.strip()))
        except ValueError:
            continue
    networks.sort(key=lambda pair: (-pair[0].prefixlen, str(pair[0]), pair[1]))
    adapters = payload.get('网络信息')
    if isinstance(adapters, Mapping):
        adapters = [adapters]
    sites = []
    for adapter in adapters if isinstance(adapters, list) else []:
        if not isinstance(adapter, Mapping):
            continue
        addresses = adapter.get('IP地址')
        addresses = addresses if isinstance(addresses, list) else re.split(r'[,;，；\s]+', str(addresses or ''))
        for address in addresses:
            try:
                ip = ip_address(str(address).strip())
            except ValueError:
                continue
            for network, site in networks:
                if ip.version == network.version and ip in network:
                    if site not in sites:
                        sites.append(site)
                    break
    return sites


def build_pc_enrichment(computer: Computer, payload: Mapping, *, site_ip_prefixes=None, personnel_roster=None) -> dict:
    system = payload.get('系统信息概览')
    system = system if isinstance(system, Mapping) else {}
    raw_login = system.get('当前登录用户工号')
    login = normalize_login(raw_login)
    from net.devices.pc.matching import match_person, personnel_snapshot
    from types import SimpleNamespace
    row, person_state = match_person(system, personnel_snapshot() if personnel_roster is None else personnel_roster)
    person = SimpleNamespace(pk=row['id'], **row) if row else None
    account, account_state = _match(Domain_Account.objects.filter(
        Q(login_name__iexact=login) | Q(login_name__iexact=raw_login or '')
        | Q(login_name__istartswith=login + '@') | Q(login_name__iendswith='\\' + login),
    ), bool(login))
    domain_computer, computer_state = _match(Domain_Computer.objects.filter(
        computer_name__iexact=computer.computer_name,
    ))
    prefixes = (getattr(settings, 'PC_SITE_IP_PREFIXES', {})
                if site_ip_prefixes is None else site_ip_prefixes)
    sites = _sites(payload, prefixes if isinstance(prefixes, Mapping) else {})
    return {
        'login_identifier': login,
        'employee_number': person.employee_id if person else '',
        'personnel_id': str(person.pk) if person else '',
        'personnel_name': person.name or '' if person else '',
        'department': person.department or '' if person else '',
        'personnel_match': person_state,
        'personnel_active': person.is_active if person else None,
        'domain_account_match': account_state,
        'domain_account_id': str(account.pk) if account else '',
        'domain_login_name': account.login_name if account else '',
        'domain_account_active': account.is_active if account else None,
        'user_ou': _ou(account),
        'domain_computer_match': computer_state,
        'domain_computer_id': str(domain_computer.pk) if domain_computer else '',
        'domain_computer_active': domain_computer.is_active if domain_computer else None,
        'computer_ou': _ou(domain_computer),
        'site': sites[0] if len(sites) == 1 else '',
        'sites': sites,
        'site_match': 'matched' if len(sites) == 1 else 'ambiguous' if sites else 'unmatched',
    }
