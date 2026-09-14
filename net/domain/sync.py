import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.db import transaction
from django.utils import timezone
from ldap3.core.exceptions import LDAPInvalidDnError
from ldap3.utils.dn import parse_dn

from net.domain.client import DomainClient
from net.models import Domain_Account, Domain_Computer, Domain_Group
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
    if isinstance(value, (list, tuple)):
        value = value[0] if len(value) == 1 else None
    if isinstance(value, datetime):
        if value.year <= 1601:
            return None
        if timezone.is_naive(value):
            value = value.replace(tzinfo=datetime_timezone.utc)
        try:
            return value.astimezone(timezone.get_default_timezone()).date()
        except (OverflowError, ValueError):
            return None
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    epoch = datetime(1601, 1, 1, tzinfo=datetime_timezone.utc)
    try:
        return _filetime_to_date(epoch + timedelta(microseconds=value // 10))
    except OverflowError:
        return None


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


def _group_classification(value):
    try:
        flags = int(value or 0)
    except (TypeError, ValueError):
        flags = 0
    if flags & 0x00000008:
        scope = Domain_Group.Scope.UNIVERSAL
    elif flags & 0x00000004:
        scope = Domain_Group.Scope.DOMAIN_LOCAL
    elif flags & 0x00000002:
        scope = Domain_Group.Scope.GLOBAL
    else:
        scope = Domain_Group.Scope.UNKNOWN
    category = (
        Domain_Group.Category.SECURITY
        if flags & 0x80000000
        else Domain_Group.Category.DISTRIBUTION
    )
    return scope, category


def _member_count(value):
    if value in (None, ''):
        return 0
    return len(value) if isinstance(value, (list, tuple)) else 1


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
    return apply_domain_snapshot(fetch_domain_snapshot(config))


def fetch_domain_snapshot(config):
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
        group_entries = connection.extend.standard.paged_search(
            search_base=config.base_dn,
            search_filter=config.group_filter,
            attributes=[
                'name', 'sAMAccountName', 'description', 'distinguishedName',
                'objectGUID', 'groupType', 'member',
            ],
            paged_size=500,
            generator=True,
        )
        groups = [
            _entry_attributes(entry)
            for entry in group_entries
            if entry.get('type') == 'searchResEntry'
        ]
    finally:
        connection.unbind()

    return users, computers, groups


def apply_domain_snapshot(snapshot):
    users, computers, groups = snapshot
    seen_accounts = set()
    seen_computers = set()
    reported_accounts = set()
    reported_computers = set()
    reported_groups = set()
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

        for attrs in groups:
            distinguished_name = _distinguished_name(attrs.get('distinguishedName'))
            if not distinguished_name:
                continue
            group_name = str(attrs.get('name') or '').strip()
            if not group_name:
                continue
            object_guid = _object_guid(attrs.get('objectGUID'))
            scope, category = _group_classification(attrs.get('groupType'))
            defaults = {
                'group_name': group_name,
                'login_name': str(attrs.get('sAMAccountName') or '').strip(),
                'description': str(attrs.get('description') or '').strip(),
                'ou': _ou_from_dn(distinguished_name),
                'group_scope': scope,
                'group_category': category,
                'member_count': _member_count(attrs.get('member')),
            }
            if object_guid is not None:
                defaults['object_guid'] = object_guid
            _update_or_create_domain_object(
                Domain_Group,
                'distinguished_name',
                distinguished_name,
                object_guid,
                defaults,
            )
            reported_groups.add(distinguished_name)

        if seen_accounts:
            Domain_Account.objects.exclude(login_name__in=seen_accounts).update(is_active=False)
        if seen_computers:
            Domain_Computer.objects.exclude(computer_name__in=seen_computers).update(is_active=False)

    return len(reported_accounts), len(reported_computers), len(reported_groups)
