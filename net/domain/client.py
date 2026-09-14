from dataclasses import dataclass, field
import ssl

import ldap3
from django.core.exceptions import ValidationError
from ldap3 import BASE, MODIFY_ADD, MODIFY_REPLACE
from ldap3.core.exceptions import (
    LDAPAssertionFailedResult, LDAPException, LDAPInvalidDnError,
    LDAPUnavailableCriticalExtensionResult,
)
from ldap3.operation.search import compile_filter, parse_filter
from ldap3.utils.dn import parse_dn
from ldap3.utils.asn1 import encode

from net.domain.validation import validate_dn_within_base, validate_domain_action


ACCOUNTDISABLE = 0x0002
DONT_EXPIRE_PASSWORD = 0x10000
PASSWORD_ACTIONS = frozenset({'create_user', 'reset_password'})
ASSERTION_CONTROL_OID = '1.3.6.1.1.12'
MAX_UAC_WRITE_ATTEMPTS = 3


@dataclass(frozen=True)
class DomainActionResult:
    success: bool
    error_message: str = ''
    stage: str = ''
    details: dict = field(default_factory=dict)


def normalize_ldap_port(value, use_ssl):
    """Accept historic form values while always passing an integer to ldap3."""
    if isinstance(value, (list, tuple)):
        value = value[0] if len(value) == 1 else None
    try:
        port = int(value)
    except (TypeError, ValueError):
        port = 636 if use_ssl else 389
    return port if 1 <= port <= 65535 else (636 if use_ssl else 389)


def effective_bind_username(config):
    username = (getattr(config, 'bind_username', '') or '').strip()
    if not username or '@' in username or '\\' in username or ('=' in username and ',' in username):
        return username
    try:
        components = parse_dn(str(getattr(config, 'base_dn', '') or '').strip(), escape=True, strip=True)
    except Exception:
        components = []
    domain_parts = [value for attribute, value, _ in components if attribute.casefold() == 'dc' and value]
    return f'{username}@{".".join(domain_parts)}' if domain_parts else username


class DomainClient:
    NONE = ldap3.NONE

    def __init__(self, config, connection_factory=None):
        self.config = config
        self.connection_factory = connection_factory

    def connect(self):
        use_ssl = bool(getattr(self.config, 'use_ssl', False))
        tls = ldap3.Tls(validate=ssl.CERT_REQUIRED) if use_ssl else None
        server = ldap3.Server(
            self.config.host,
            port=normalize_ldap_port(getattr(self.config, 'port', None), use_ssl),
            use_ssl=use_ssl,
            get_info=ldap3.NONE,
            connect_timeout=8,
            tls=tls,
        )
        username = effective_bind_username(self.config)
        # Do not cycle credentials/authentication methods: failed binds can lock AD users.
        authentication = ldap3.NTLM if '\\' in username and '=' not in username else ldap3.SIMPLE
        return ldap3.Connection(
            server,
            user=username,
            password=self.config.bind_password,
            authentication=authentication,
            auto_bind=True,
            receive_timeout=20,
        )

    def execute(
        self, object_type, action, target_dn, parameters, *, password=None,
        recovery_stage=None,
    ):
        parameters = validate_domain_action(object_type, action, parameters)
        target_dn = parameters['user_dn'] if action == 'create_user' else target_dn
        target_dn = validate_dn_within_base(target_dn, self.config.base_dn)
        for parameter_name in ('destination_dn', 'group_dn'):
            if parameter_name in parameters:
                parameters[parameter_name] = validate_dn_within_base(
                    parameters[parameter_name], self.config.base_dn,
                )
        if action in PASSWORD_ACTIONS:
            if not isinstance(password, str):
                raise ValidationError('密码操作缺少一次性密码载荷。')
            self._require_verified_password_transport()
            if action == 'create_user':
                parameters['cn'] = self._first_cn_value(target_dn)
                if recovery_stage not in (None, 'user_created_password_pending'):
                    raise ValidationError('新增用户恢复阶段无效。')
        elif recovery_stage is not None:
            raise ValidationError('当前操作不接受恢复阶段。')

        connection = None
        try:
            connection = (
                self.connection_factory()
                if self.connection_factory is not None else self.connect()
            )
        except Exception as exc:
            return self._exception_result(exc)
        try:
            return self._execute(
                connection, action, target_dn, parameters, password,
                recovery_stage=recovery_stage,
            )
        except LDAPException as exc:
            if action == 'create_user':
                return DomainActionResult(
                    False, '新增用户结果不确定，需要人工核查。',
                    stage='manual_intervention_required',
                    details={'distinguished_name': target_dn},
                )
            return self._exception_result(exc)
        finally:
            if connection is not None:
                try:
                    connection.unbind()
                except Exception:
                    pass

    def _execute(
        self, connection, action, target_dn, parameters, password, *,
        recovery_stage=None,
    ):
        if action == 'create_user':
            if recovery_stage == 'user_created_password_pending':
                if not self._created_user_matches(connection, target_dn, parameters):
                    return DomainActionResult(
                        False, '目录对象身份与待恢复用户不一致，需要人工核查。',
                        stage='manual_intervention_required',
                        details={'distinguished_name': target_dn},
                    )
            else:
                try:
                    created = connection.add(
                        target_dn,
                        ['top', 'person', 'organizationalPerson', 'user'],
                        {
                            'cn': parameters['cn'],
                            'sAMAccountName': parameters['login_name'],
                            'displayName': parameters['display_name'],
                        },
                    )
                except Exception:
                    return DomainActionResult(
                        False, '新增用户结果不确定，需要人工核查。',
                        stage='manual_intervention_required',
                        details={'distinguished_name': target_dn},
                    )
                if not created:
                    result_code = (getattr(connection, 'result', {}) or {}).get('result')
                    if (
                        not isinstance(result_code, int)
                        or isinstance(result_code, bool)
                        or result_code == 0
                        or result_code in {51, 52, 81, 82, 85, 91}
                    ):
                        return DomainActionResult(
                            False, '新增用户结果不确定，需要人工核查。',
                            stage='manual_intervention_required',
                            details={'distinguished_name': target_dn},
                        )
                    return self._result(connection)
            try:
                password_result = self._modify(
                    connection, target_dn,
                    {'unicodePwd': [(MODIFY_REPLACE, [self._unicode_password(password)])]},
                )
            except Exception:
                password_result = DomainActionResult(False)
            if not password_result.success:
                return DomainActionResult(
                    False, '用户已创建，但密码初始化失败；请提交新密码后重试。',
                    stage='user_created_password_pending',
                    details={'distinguished_name': target_dn},
                )
            return DomainActionResult(
                True,
                stage='completed',
                details={'distinguished_name': target_dn},
            )
        if action == 'move_ou':
            relative_dn = self._relative_dn(target_dn)
            succeeded = connection.modify_dn(
                target_dn, relative_dn=relative_dn,
                new_superior=parameters['destination_dn'],
            )
            result = self._result(connection, succeeded)
            if not result.success:
                return result
            return DomainActionResult(True, details={
                'distinguished_name': f'{relative_dn},{parameters["destination_dn"]}',
                'ou': parameters['destination_dn'],
            })
        if action == 'add_group':
            operation = MODIFY_ADD
            return self._modify(
                connection, parameters['group_dn'],
                {'member': [(operation, [target_dn])]},
            )
        if action == 'reset_password':
            return self._modify(
                connection, target_dn,
                {'unicodePwd': [(MODIFY_REPLACE, [self._unicode_password(password)])]},
            )
        if action == 'must_change_password':
            value = 0 if parameters['enabled'] else -1
            return self._modify(connection, target_dn, {'pwdLastSet': [(MODIFY_REPLACE, [value])]})
        if action == 'unlock':
            return self._modify(connection, target_dn, {'lockoutTime': [(MODIFY_REPLACE, [0])]})
        if action in {'enable', 'disable', 'password_never_expires'}:
            return self._modify_uac(connection, target_dn, action, parameters.get('enabled'))
        raise ValidationError('不支持此类目录对象的操作。')

    @staticmethod
    def _unicode_password(password):
        return f'"{password}"'.encode('utf-16-le')

    def _created_user_matches(self, connection, target_dn, parameters):
        try:
            found = connection.search(
                target_dn, '(objectClass=*)', BASE,
                attributes=['sAMAccountName', 'displayName'],
            )
            entry = connection.entries[0] if found and len(connection.entries) == 1 else None
            login_name = entry.sAMAccountName.value if entry is not None else None
            display_name = entry.displayName.value if entry is not None else None
        except Exception:
            return False
        return (
            isinstance(login_name, str)
            and login_name.casefold() == parameters['login_name'].casefold()
            and display_name == parameters['display_name']
        )

    def _require_verified_password_transport(self):
        if not bool(getattr(self.config, 'use_ssl', False)):
            raise ValidationError('密码操作必须使用经过验证的 LDAPS/TLS 连接。')

    @staticmethod
    def _first_cn_value(value):
        try:
            parsed = parse_dn(value, escape=True, strip=True)
        except (LDAPInvalidDnError, TypeError, ValueError):
            raise ValidationError('新增用户名称必须使用 CN 作为首个 RDN。') from None
        first_rdn = []
        for attribute, component, separator in parsed:
            first_rdn.append((attribute, component))
            if separator != '+':
                break
        if (
            len(first_rdn) != 1
            or first_rdn[0][0].casefold() != 'cn'
            or not first_rdn[0][1]
        ):
            raise ValidationError('新增用户名称必须使用 CN 作为首个 RDN。')
        return DomainClient._unescape_dn_value(first_rdn[0][1])

    @staticmethod
    def _unescape_dn_value(value):
        decoded = bytearray()
        index = 0
        while index < len(value):
            if value[index] != '\\':
                decoded.extend(value[index].encode('utf-8'))
                index += 1
                continue
            if index + 1 >= len(value):
                raise ValidationError('新增用户名称必须使用 CN 作为首个 RDN。')
            escaped = value[index + 1:index + 3]
            if len(escaped) == 2 and all(char in '0123456789abcdefABCDEF' for char in escaped):
                decoded.append(int(escaped, 16))
                index += 3
            else:
                decoded.extend(value[index + 1].encode('utf-8'))
                index += 2
        try:
            return decoded.decode('utf-8')
        except UnicodeDecodeError:
            raise ValidationError('新增用户名称必须使用 CN 作为首个 RDN。') from None

    @staticmethod
    def _relative_dn(value):
        parsed = parse_dn(value, escape=True, strip=True)
        components = []
        for attribute, component, separator in parsed:
            components.append(f'{attribute}={component}')
            if separator != '+':
                break
        return '+'.join(components)

    def _modify_uac(self, connection, target_dn, action, enabled):
        for _ in range(MAX_UAC_WRITE_ATTEMPTS):
            current, error = self._read_uac(connection, target_dn)
            if error is not None:
                return error
            value = self._uac_value(current, action, enabled)
            try:
                result = self._modify(
                    connection, target_dn,
                    {'userAccountControl': [(MODIFY_REPLACE, [value])]},
                    controls=[self._uac_assertion_control(current)],
                )
            except LDAPAssertionFailedResult:
                continue
            except LDAPUnavailableCriticalExtensionResult:
                return self._modify_uac_compatible(connection, target_dn, action, enabled)
            if not result.success and (connection.result or {}).get('result') == 12:
                return self._modify_uac_compatible(connection, target_dn, action, enabled)
            if result.success or not self._is_assertion_failure(connection):
                return result
        return DomainActionResult(False, '目录对象状态发生并发变化，请重试。')

    def _read_uac(self, connection, target_dn):
        found = connection.search(target_dn, '(objectClass=*)', BASE, attributes=['userAccountControl'])
        if not found:
            result = self._result(connection, False)
            if (connection.result or {}).get('result') == 0:
                result = DomainActionResult(False, '未找到目录对象或无法读取其状态，请重新同步后重试。')
            return None, result
        try:
            raw = connection.entries[0].userAccountControl.value
            if isinstance(raw, (list, tuple)):
                raw = raw[0] if len(raw) == 1 else None
            if isinstance(raw, bool):
                raise ValueError
            current = int(raw)
            if current < 0:
                raise ValueError
            return current, None
        except (AttributeError, IndexError, TypeError, ValueError):
            return None, DomainActionResult(False, '无法读取目录对象状态。')

    @staticmethod
    def _uac_value(current, action, enabled):
        if action == 'enable':
            return current & ~ACCOUNTDISABLE
        if action == 'disable':
            return current | ACCOUNTDISABLE
        return current | DONT_EXPIRE_PASSWORD if enabled else current & ~DONT_EXPIRE_PASSWORD

    def _modify_uac_compatible(self, connection, target_dn, action, enabled):
        # Windows AD rejects RFC4528 with unavailableCriticalExtension (12),
        # which guarantees that the rejected request did not modify the object.
        # Re-read before the ordinary LDAP modify, as in ad_core.py. This path
        # cannot offer atomic compare-and-swap against external administrators.
        current, error = self._read_uac(connection, target_dn)
        if error is not None:
            return error
        value = self._uac_value(current, action, enabled)
        if value == current:
            return DomainActionResult(True)
        result = self._modify(connection, target_dn, {'userAccountControl': [(MODIFY_REPLACE, [value])]})
        if not result.success:
            return result
        actual, error = self._read_uac(connection, target_dn)
        if error is not None or actual != value:
            return DomainActionResult(False, '已提交状态修改，但回读核对失败，请同步目录核实后再操作。')
        return DomainActionResult(True)

    @staticmethod
    def _uac_assertion_control(current):
        filter_node = parse_filter(
            f'(userAccountControl={current})', None,
            auto_escape=False, auto_encode=False,
            validator=None, check_names=False,
        ).elements[0]
        return (ASSERTION_CONTROL_OID, True, encode(compile_filter(filter_node)))

    @staticmethod
    def _is_assertion_failure(connection):
        return (getattr(connection, 'result', {}) or {}).get('result') == 122

    def _modify(self, connection, target_dn, changes, *, controls=None):
        return self._result(connection, connection.modify(target_dn, changes, controls=controls))

    @staticmethod
    def _result(connection, succeeded=None):
        result = getattr(connection, 'result', {}) or {}
        if succeeded is None:
            succeeded = result.get('result') == 0
        if succeeded:
            return DomainActionResult(True)
        return DomainClient._error_result(result.get('result'))

    @staticmethod
    def _exception_result(exc):
        code = getattr(exc, 'result', None)
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return DomainClient._error_result(code)
        return DomainActionResult(False, 'LDAP 连接或操作失败。')

    @staticmethod
    def _error_result(code):
        messages = {
            8: '域控要求更强的身份验证，请使用 LDAPS 或符合域策略的认证方式。',
            12: '域控不支持请求的 LDAP 扩展控件。',
            19: '域控约束或密码策略不满足，请检查密码复杂度、历史限制和对象属性。',
            20: '属性值已存在；加入分组时请检查对象是否已经在该组中。',
            32: '目录对象不存在。',
            49: '域控认证失败，请检查账号、密码及账号锁定或停用状态。',
            50: 'LDAP 权限不足。',
            51: '域控正忙，请稍后重试。',
            52: '域控服务不可用，请检查连接。',
            53: 'LDAP 当前拒绝执行该操作。',
            64: '目录名称无效。',
            68: '目录对象已存在。',
        }
        return DomainActionResult(False, messages.get(code, 'LDAP 操作失败。'))
