"""Local directory membership mirrors; no network I/O in these helpers."""
from collections import defaultdict

from django.core.exceptions import ValidationError
from ldap3.protocol.formatters.formatters import format_sid

from net.models import Domain_Account, Domain_Computer, Domain_Group, DomainMembership, DomainOU
from .validation import _parse_rdn_components, validate_dn_within_base


def dn_key(value):
    try:
        return _parse_rdn_components(value)
    except ValidationError:
        return None


def sid(value):
    if isinstance(value, bytes):
        try:
            value = format_sid(value)
        except (ValueError, IndexError, TypeError):
            return ''
    return value if isinstance(value, str) and value.startswith('S-') else ''


def refresh_group_names(objects):
    for model, rows in objects:
        names = defaultdict(list)
        field = 'account' if model is Domain_Account else 'computer'
        memberships = DomainMembership.objects.filter(**{field + '_id__in': [row.pk for row in rows]}).select_related('group').order_by('group__group_name', 'group__distinguished_name')
        for membership in memberships:
            names[getattr(membership, field + '_id')].append(
                membership.group.group_name + ('（主组）' if membership.is_primary else ''))
        for row in rows:
            row.group_names = '；'.join(names[row.pk])
        if rows:
            model.objects.bulk_update(rows, ['group_names'], batch_size=500)


def publish_memberships(users, computers, groups):
    # The caller publishes the complete LDAP snapshot in one short transaction.
    group_map = {dn_key(row.distinguished_name): row for row in Domain_Group.objects.all()}
    objects = [(Domain_Account, list(Domain_Account.objects.all())),
               (Domain_Computer, list(Domain_Computer.objects.all()))]
    targets = {dn_key(row.distinguished_name): (field, row.pk)
               for (model, rows), field in zip(objects, ('account', 'computer'))
               for row in rows if dn_key(row.distinguished_name)}
    links = {}
    seen_groups = set()
    by_sid = {}
    for attrs in groups:
        group = group_map.get(dn_key(attrs.get('distinguishedName')))
        if group is None:
            continue
        seen_groups.add(group.pk)
        if sid(attrs.get('objectSid')):
            by_sid[sid(attrs['objectSid'])] = group
        members = attrs.get('member') or []
        if isinstance(members, str):
            members = [members]
        for member in members:
            target = targets.get(dn_key(member))
            if target:
                field, target_id = target
                links[(group.pk, field, target_id)] = False
    for attrs in [*users, *computers]:
        object_sid = sid(attrs.get('objectSid'))
        primary_id = str(attrs.get('primaryGroupID') or '')
        group = by_sid.get(object_sid.rsplit('-', 1)[0] + '-' + primary_id) if object_sid else None
        target = targets.get(dn_key(attrs.get('distinguishedName')))
        if group and target:
            field, target_id = target
            links[(group.pk, field, target_id)] = True
    Domain_Group.objects.exclude(pk__in=seen_groups).update(is_available=False)
    DomainMembership.objects.all().delete()
    DomainMembership.objects.bulk_create([
        DomainMembership(group_id=group_id, is_primary=primary, **{field + '_id': target_id})
        for (group_id, field, target_id), primary in links.items()
    ], batch_size=500)
    refresh_group_names(objects)


def publish_ous(ous):
    from .sync import _object_guid, _distinguished_name, _update_or_create_domain_object
    seen = []
    for attrs in ous:
        dn = _distinguished_name(attrs.get('distinguishedName'))
        if not dn:
            continue
        guid = _object_guid(attrs.get('objectGUID'))
        defaults = {'name': str(attrs.get('name') or dn), 'is_available': True}
        if guid:
            defaults['object_guid'] = guid
        obj = _update_or_create_domain_object(DomainOU, 'distinguished_name', dn, guid, defaults)
        seen.append(obj.pk)
    DomainOU.objects.exclude(pk__in=seen).update(is_available=False)


def directory_choices(model, base_dn, *, security_only=False):
    rows = model.objects.filter(is_available=True).order_by('distinguished_name')
    if security_only:
        rows = rows.filter(group_category='security')
    choices = []
    for row in rows:
        try:
            validate_dn_within_base(row.distinguished_name, base_dn)
        except ValidationError:
            continue
        name = row.group_name if model is Domain_Group else row.name
        choices.append((row.distinguished_name, f'{name} — {row.distinguished_name}'))
    return choices


def validate_directory_selection(action, parameters, config, rows=(), object_type=None):
    if action == 'move_group':
        validate_directory_selection('remove_group', {'group_dn': parameters['source_group_dn']}, config, rows, object_type)
        target_dn = validate_dn_within_base(parameters['group_dn'], config.base_dn)
        if dn_key(target_dn) == dn_key(parameters['source_group_dn']):
            raise ValidationError('目标分组不能是当前分组。')
        if not Domain_Group.objects.filter(distinguished_name=target_dn, is_available=True).exists():
            raise ValidationError('请选择当前域同步到的有效目标分组。')
        return
    if action not in {'add_group', 'remove_group', 'move_ou'}:
        return
    model, field = (DomainOU, 'destination_dn') if action == 'move_ou' else (Domain_Group, 'group_dn')
    dn = validate_dn_within_base(parameters[field], config.base_dn)
    selected = model.objects.filter(distinguished_name=dn, is_available=True).first()
    if selected is None or (action == 'add_group' and selected.group_category != 'security'):
        raise ValidationError('请选择当前域同步到的有效 OU 或安全组，目录变化后请重新同步。')
    if action == 'remove_group':
        member_field = 'account' if object_type == 'account' else 'computer'
        memberships = DomainMembership.objects.filter(group=selected, **{member_field + '_id__in': [row.pk for row in rows]})
        if memberships.filter(is_primary=True).exists():
            raise ValidationError('不能直接移出 AD 主组。')
        if memberships.count() != len(rows):
            raise ValidationError('所选目标不是该分组的已同步直接成员，请刷新成员列表。')
