from collections.abc import Mapping

from django.core.exceptions import ValidationError
from ldap3.core.exceptions import LDAPInvalidDnError
from ldap3.utils.dn import parse_dn


MAX_DOMAIN_OPERATION_TARGETS = 500

_ACTION_PARAMETERS = {
    'account': {
        'create_user': frozenset({'user_dn', 'login_name', 'display_name'}),
        'move_ou': frozenset({'destination_dn'}),
        'add_group': frozenset({'group_dn'}),
        'remove_group': frozenset({'group_dn'}),
        'move_group': frozenset({'source_group_dn', 'group_dn'}),
        'reset_password': frozenset(),
        'must_change_password': frozenset({'enabled'}),
        'password_never_expires': frozenset({'enabled'}),
        'unlock': frozenset(),
        'enable': frozenset(),
        'disable': frozenset(),
    },
    'computer': {
        'move_ou': frozenset({'destination_dn'}),
        'add_group': frozenset({'group_dn'}),
        'remove_group': frozenset({'group_dn'}),
        'move_group': frozenset({'source_group_dn', 'group_dn'}),
        'enable': frozenset(),
        'disable': frozenset(),
    },
}


def _parse_rdn_components(value):
    try:
        parsed = parse_dn(str(value or '').strip(), escape=True, strip=True)
    except (LDAPInvalidDnError, TypeError, ValueError):
        raise ValidationError('目录名称格式无效。') from None
    if not parsed:
        raise ValidationError('目录名称格式无效。')

    rdns = []
    current = []
    for attribute, component, separator in parsed:
        if not attribute or component is None:
            raise ValidationError('目录名称格式无效。')
        current.append((attribute.casefold(), component.casefold()))
        if separator != '+':
            rdns.append(tuple(sorted(current)))
            current = []
    if current:
        rdns.append(tuple(sorted(current)))
    return tuple(rdns)


def validate_dn(value):
    """Validate an LDAP DN without reflecting supplied directory data."""
    _parse_rdn_components(value)
    return str(value).strip()


def validate_dn_within_base(value, base_dn):
    """Require a DN to end with whole parsed base-DN RDN components."""
    candidate = _parse_rdn_components(value)
    base = _parse_rdn_components(base_dn)
    if len(candidate) < len(base) or candidate[-len(base):] != base:
        raise ValidationError('目录名称不在允许的域范围内。')
    return str(value).strip()


def validate_domain_action(object_type, action, parameters):
    """Return a normalized non-sensitive parameter mapping for an allowed action."""
    allowed_actions = _ACTION_PARAMETERS.get(object_type)
    required_parameters = allowed_actions.get(action) if allowed_actions else None
    if required_parameters is None:
        raise ValidationError('不支持此类目录对象的操作。')
    if not isinstance(parameters, Mapping):
        raise ValidationError('操作参数格式无效。')

    normalized = dict(parameters)
    target_count = normalized.pop('target_count', None)
    if target_count is not None:
        if isinstance(target_count, bool) or not isinstance(target_count, int):
            raise ValidationError('目标数量无效。')
        if not 1 <= target_count <= MAX_DOMAIN_OPERATION_TARGETS:
            raise ValidationError('目标数量超出允许范围。')

    if set(normalized) != required_parameters:
        raise ValidationError('操作参数不符合该动作要求。')
    for key in ('user_dn', 'destination_dn', 'group_dn', 'source_group_dn'):
        if key in normalized:
            normalized[key] = validate_dn(normalized[key])
    if 'enabled' in normalized and type(normalized['enabled']) is not bool:
        raise ValidationError('开关参数必须为布尔值。')
    return normalized
