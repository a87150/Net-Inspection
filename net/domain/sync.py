import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.db import transaction
from ldap3.core.exceptions import LDAPInvalidDnError
from ldap3.utils.dn import parse_dn

from net.domain.client import DomainClient
from net.models import Domain_Account, Domain_Computer
def _connect(config):
    return DomainClient(config).connect()


def test_domain_connection(config):
    connection = _connect(config)
    try:
        if not connection.bound:
            raise RuntimeError('LDAP 绑定失败')
        return f'已连接 {config.host}:{config.port}'
    finally:
        connection.unbind()


def _filetime_to_date(value):
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    epoch = datetime(1601, 1, 1, tzinfo=datetime_timezone.utc)
    return (epoch + timedelta(microseconds=value / 10)).date()


def _ou_from_dn(value):
    parts = str(value or '').split(',')
    return ','.join(parts[1:]) if len(parts) > 1 else ''


def _entry_attributes(entry):
    if not isinstance(entry, dict):
        return {}
    normalized = {}
    for name, value in entry.get('attributes', {}).items():
        if isinstance(value, (list, tuple)):
            if not value:
                normalized[name] = None
            elif len(value) == 1:
                normalized[name] = value[0]
            else:
                normalized[name] = list(value)
        else:
            normalized[name] = value
    return normalized


def _object_guid(value):
    if value in (None, ''):
        return None
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, bytes):
        if len(value) == 16:
            return uuid.UUID(bytes_le=value)
        try:
            value = value.decode('ascii')
        except UnicodeDecodeError:
            return None
    try:
        return uuid.UUID(str(value).strip())
    except (AttributeError, TypeError, ValueError):
        return None


def _distinguished_name(value):
    normalized = str(value or '').strip()
    if not normalized:
        return None
    try:
        parsed = parse_dn(normalized, escape=True)
    except (LDAPInvalidDnError, TypeError, ValueError):
        return None
    return normalized if parsed else None


def _update_or_create_domain_object(model, identity_field, identity, object_guid, defaults):
    existing_by_identity = model.objects.filter(
        **{identity_field: identity},
    ).first()
    if object_guid is not None:
        existing = model.objects.filter(object_guid=object_guid).first()
        if existing is not None:
            for field_name, value in defaults.items():
                setattr(existing, field_name, value)
            update_fields = list(defaults)
            if existing_by_identity is None or existing_by_identity.pk == existing.pk:
                setattr(existing, identity_field, identity)
                update_fields.append(identity_field)
            existing.save(update_fields=update_fields)
            return existing
    if existing_by_identity is not None:
        for field_name, value in defaults.items():
            setattr(existing_by_identity, field_name, value)
        existing_by_identity.save(update_fields=list(defaults))
        return existing_by_identity
    return model.objects.create(**{identity_field: identity, **defaults})


def sync_domain(config):
    connection = _connect(config)
    try:
        user_entries = connection.extend.standard.paged_search(
            search_base=config.base_dn,
            search_filter=config.user_filter,
            attributes=['displayName', 'sAMAccountName', 'userAccountControl', 'mail', 'distinguishedName', 'objectGUID', 'lastLogonTimestamp', 'userWorkstations'],
            paged_size=500,
            generator=True,
        )
        users = [_entry_attributes(entry) for entry in user_entries if entry.get('type') == 'searchResEntry']
        computer_entries = connection.extend.standard.paged_search(
            search_base=config.base_dn,
            search_filter=config.computer_filter,
            attributes=['name', 'operatingSystem', 'userAccountControl', 'distinguishedName', 'objectGUID', 'lastLogonTimestamp'],
            paged_size=500,
            generator=True,
        )
        computers = [_entry_attributes(entry) for entry in computer_entries if entry.get('type') == 'searchResEntry']
    finally:
        connection.unbind()

    seen_accounts = set()
    seen_computers = set()
    reported_accounts = set()
    reported_computers = set()
    with transaction.atomic():
        for attrs in users:
            login_name = str(attrs.get('sAMAccountName') or '').strip()
            if not login_name:
                continue
            flags = int(attrs.get('userAccountControl') or 0)
            object_guid = _object_guid(attrs.get('objectGUID'))
            distinguished_name = _distinguished_name(attrs.get('distinguishedName'))
            defaults = {
                'account_name': str(attrs.get('displayName') or login_name),
                'is_active': not bool(flags & 2),
                'allowed_workstations': str(attrs.get('userWorkstations') or ''),
                'last_login_date': _filetime_to_date(attrs.get('lastLogonTimestamp')),
            }
            if object_guid is not None:
                defaults['object_guid'] = object_guid
            if distinguished_name is not None:
                defaults['distinguished_name'] = distinguished_name
                defaults['ou'] = _ou_from_dn(distinguished_name)
            account = _update_or_create_domain_object(
                Domain_Account,
                'login_name',
                login_name,
                object_guid,
                defaults,
            )
            seen_accounts.update({login_name, account.login_name})
            reported_accounts.add(login_name)

        for attrs in computers:
            computer_name = str(attrs.get('name') or '').strip()
            if not computer_name:
                continue
            flags = int(attrs.get('userAccountControl') or 0)
            object_guid = _object_guid(attrs.get('objectGUID'))
            distinguished_name = _distinguished_name(attrs.get('distinguishedName'))
            defaults = {
                'is_active': not bool(flags & 2),
                'os': str(attrs.get('operatingSystem') or ''),
                'last_login_date': _filetime_to_date(attrs.get('lastLogonTimestamp')),
            }
            if object_guid is not None:
                defaults['object_guid'] = object_guid
            if distinguished_name is not None:
                defaults['distinguished_name'] = distinguished_name
                defaults['ou'] = _ou_from_dn(distinguished_name)
            computer = _update_or_create_domain_object(
                Domain_Computer,
                'computer_name',
                computer_name,
                object_guid,
                defaults,
            )
            seen_computers.update({computer_name, computer.computer_name})
            reported_computers.add(computer_name)

        if seen_accounts:
            Domain_Account.objects.exclude(login_name__in=seen_accounts).update(is_active=False)
        if seen_computers:
            Domain_Computer.objects.exclude(computer_name__in=seen_computers).update(is_active=False)

    return len(reported_accounts), len(reported_computers)
