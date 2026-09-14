import ssl
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from ldap3 import BASE
from ldap3.core.exceptions import LDAPException
from ldap3.protocol.rfc4511 import Filter
from ldap3.utils.asn1 import decoder

from net.domain.validation import (
    validate_dn_within_base,
    validate_domain_action,
)
from net.domain.client import DomainClient, effective_bind_username
from net.domain import sync as ad_sync


class UacConflictConnection:
    def __init__(self, current, *, conflicts=1, external_bit=0x400000):
        self.current = current
        self.conflicts = conflicts
        self.external_bit = external_bit
        self.result = {'result': 0}
        self.controls = []
        self.search_count = 0
        self.unbind_count = 0
        self.entries = []

    def search(self, *_args, **_kwargs):
        self.search_count += 1
        self.entries = [SimpleNamespace(
            userAccountControl=SimpleNamespace(value=self.current),
        )]
        return True

    def modify(self, _dn, changes, controls=None):
        self.controls.append(controls)
        if self.conflicts:
            self.conflicts -= 1
            self.current |= self.external_bit
            self.result = {'result': 122}
            return False
        self.current = changes['userAccountControl'][0][1][0]
        self.result = {'result': 0}
        return True

    def unbind(self):
        self.unbind_count += 1


class DomainActionValidationTests(SimpleTestCase):
    def test_computer_action_allowlist_rejects_account_only_password_flag(self):
        """Removing the computer allowlist would permit a password flag on a computer."""
        parameters = {
            'move_ou': {'destination_dn': 'OU=Workstations,DC=example,DC=test'},
            'add_group': {'group_dn': 'CN=Operators,OU=Groups,DC=example,DC=test'},
            'enable': {},
            'disable': {},
        }

        for action, action_parameters in parameters.items():
            with self.subTest(action=action):
                validate_domain_action('computer', action, action_parameters)

        with self.assertRaises(ValidationError):
            validate_domain_action('computer', 'password_never_expires', {'enabled': True})
        for object_type in ('account', 'computer'):
            with self.assertRaises(ValidationError):
                validate_domain_action(object_type, 'remove_group', {
                    'group_dn': 'CN=Operators,OU=Groups,DC=example,DC=test',
                })

    def test_action_requires_only_its_exact_non_sensitive_parameters(self):
        """Dropping parameter validation would allow an LDAP action without its destination."""
        with self.assertRaises(ValidationError):
            validate_domain_action('computer', 'move_ou', {})
        with self.assertRaises(ValidationError):
            validate_domain_action('account', 'must_change_password', {})

        self.assertEqual(
            validate_domain_action(
                'account', 'must_change_password', {'enabled': False},
            ),
            {'enabled': False},
        )

    def test_destination_must_be_within_base_dn_by_rdn_components(self):
        """A string suffix check would accept an unrelated badexample.test DN."""
        with self.assertRaises(ValidationError):
            validate_dn_within_base(
                'OU=Outside,DC=badexample,DC=test',
                'DC=example,DC=test',
            )

        self.assertEqual(
            validate_dn_within_base(
                'OU=Engineering,DC=example,DC=test',
                'DC=example,DC=test',
            ),
            'OU=Engineering,DC=example,DC=test',
        )

    def test_destination_rejects_malformed_dn_without_exposing_input(self):
        """Passing malformed DN text to LDAP would create an unsafe operator error path."""
        with self.assertRaises(ValidationError) as error:
            validate_dn_within_base('not a dn', 'DC=example,DC=test')

        self.assertNotIn('not a dn', str(error.exception))


class DomainClientTests(SimpleTestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            host='dc.example.test',
            port=['636'],
            use_ssl=True,
            bind_username='directory-admin',
            bind_password='not-for-logs',
            base_dn='DC=example,DC=test',
        )

    @patch('ldap3.Connection')
    @patch('ldap3.Server')
    def test_connect_reuses_ssl_settings_and_normalizes_historic_list_port(self, server, connection):
        """Passing a historic form list into ldap3 would make saved LDAPS settings unusable."""
        connection.return_value.bound = True

        DomainClient(self.config).connect()

        server.assert_called_once_with(
            'dc.example.test', port=636, use_ssl=True,
            get_info=DomainClient.NONE, connect_timeout=8, tls=ANY,
        )
        self.assertEqual(connection.call_args.kwargs['user'], 'directory-admin@example.test')
        self.assertTrue(connection.call_args.kwargs['auto_bind'])

    def test_password_actions_reject_non_tls_before_any_connection_or_write(self):
        """Removing the transport gate would send a password to plain LDAP."""
        insecure_config = SimpleNamespace(**{
            **self.config.__dict__, 'use_ssl': False, 'port': 389,
        })
        connection = MagicMock()
        client = DomainClient(insecure_config)
        client.connect = MagicMock(return_value=connection)

        password_actions = (
            ('account', 'reset_password', 'CN=Alice,OU=Users,DC=example,DC=test', {}),
            ('account', 'create_user', None, {
                'user_dn': 'CN=Alice,OU=Users,DC=example,DC=test',
                'login_name': 'alice', 'display_name': 'Alice',
            }),
        )
        for object_type, action, target_dn, parameters in password_actions:
            with self.subTest(action=action):
                with self.assertRaises(ValidationError):
                    client.execute(
                        object_type, action, target_dn, parameters,
                        password='Never-Send-On-Plain-LDAP!',
                    )

        client.connect.assert_not_called()
        connection.add.assert_not_called()
        connection.modify.assert_not_called()

    @patch('ldap3.Connection')
    @patch('ldap3.Server')
    @patch('ldap3.Tls')
    def test_ldaps_connection_requires_certificate_validation(self, tls, server, connection):
        """Using ldap3's permissive default TLS validation would trust an unverified server."""
        DomainClient(self.config).connect()

        tls.assert_called_once_with(validate=ssl.CERT_REQUIRED)
        self.assertIs(server.call_args.kwargs['tls'], tls.return_value)

    def test_create_user_sets_cn_from_escaped_first_cn_rdn(self):
        """Omitting cn or copying an escaped DN value would create an invalid user object."""
        connection = MagicMock()
        connection.add.return_value = False
        connection.result = {'result': 68}
        client = DomainClient(self.config, connection_factory=lambda: connection)

        client.execute(
            'account', 'create_user', None,
            {
                'user_dn': r'CN=Smith\, Alice,OU=Users,DC=example,DC=test',
                'login_name': 'alice', 'display_name': 'Alice Smith',
            },
            password='In-Memory-Only-2948!',
        )

        attributes = connection.add.call_args.args[2]
        self.assertEqual(attributes['cn'], 'Smith, Alice')

    def test_create_user_password_failure_records_recoverable_stage(self):
        """Losing the post-add stage would make a half-created user impossible to recover safely."""
        connection = MagicMock()
        connection.add.return_value = True
        connection.modify.return_value = False
        connection.result = {'result': 53}

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Pending User,OU=Users,DC=example,DC=test',
                'login_name': 'pending.user', 'display_name': 'Pending User',
            },
            password='First-Password-Must-Not-Persist!',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, 'user_created_password_pending')
        self.assertEqual(result.details, {
            'distinguished_name': 'CN=Pending User,OU=Users,DC=example,DC=test',
        })
        self.assertEqual(result.error_message, '用户已创建，但密码初始化失败；请提交新密码后重试。')
        self.assertNotIn('First-Password-Must-Not-Persist!', repr(result))

    def test_create_user_recovery_verifies_identity_then_skips_add(self):
        """A verified pending retry must finish password setup without re-adding the user."""
        connection = MagicMock()
        connection.search.return_value = True
        connection.entries = [SimpleNamespace(
            sAMAccountName=SimpleNamespace(value='pending.user'),
            displayName=SimpleNamespace(value='Pending User'),
        )]
        connection.modify.return_value = True
        connection.result = {'result': 0}

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Pending User,OU=Users,DC=example,DC=test',
                'login_name': 'pending.user', 'display_name': 'Pending User',
            },
            password='Fresh-Recovery-Password!',
            recovery_stage='user_created_password_pending',
        )

        self.assertTrue(result.success)
        connection.add.assert_not_called()
        connection.search.assert_called_once_with(
            'CN=Pending User,OU=Users,DC=example,DC=test',
            '(objectClass=*)', BASE,
            attributes=['sAMAccountName', 'displayName'],
        )
        connection.modify.assert_called_once()

    def test_create_user_recovery_refuses_unrelated_existing_user(self):
        """Matching only the DN would let a retry take over an unrelated existing account."""
        connection = MagicMock()
        connection.search.return_value = True
        connection.entries = [SimpleNamespace(
            sAMAccountName=SimpleNamespace(value='someone.else'),
            displayName=SimpleNamespace(value='Someone Else'),
        )]

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Pending User,OU=Users,DC=example,DC=test',
                'login_name': 'pending.user', 'display_name': 'Pending User',
            },
            password='Must-Not-Be-Applied!',
            recovery_stage='user_created_password_pending',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, 'manual_intervention_required')
        self.assertEqual(result.error_message, '目录对象身份与待恢复用户不一致，需要人工核查。')
        connection.add.assert_not_called()
        connection.modify.assert_not_called()

    def test_create_user_uncertain_add_requires_manual_intervention(self):
        """Blindly retrying an interrupted add could duplicate or take over a directory object."""
        connection = MagicMock()
        connection.add.side_effect = LDAPException('connection lost after request')

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Uncertain User,OU=Users,DC=example,DC=test',
                'login_name': 'uncertain.user', 'display_name': 'Uncertain User',
            },
            password='Must-Not-Appear!',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, 'manual_intervention_required')
        self.assertEqual(result.details, {
            'distinguished_name': 'CN=Uncertain User,OU=Users,DC=example,DC=test',
        })
        self.assertEqual(result.error_message, '新增用户结果不确定，需要人工核查。')
        self.assertNotIn('connection lost after request', repr(result))
        self.assertNotIn('Must-Not-Appear!', repr(result))
        connection.modify.assert_not_called()

    def test_create_user_unexpected_add_exception_is_also_manual_intervention(self):
        """A non-ldap3 transport wrapper failure still leaves the add outcome unknown."""
        connection = MagicMock()
        connection.add.side_effect = RuntimeError('wrapper lost the add response')

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Wrapper Failure,OU=Users,DC=example,DC=test',
                'login_name': 'wrapper.failure', 'display_name': 'Wrapper Failure',
            },
            password='Never-Leak-Wrapper-Password!',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, 'manual_intervention_required')
        self.assertNotIn('wrapper lost the add response', repr(result))
        connection.modify.assert_not_called()

    def test_create_user_non_ldap_timeout_at_add_is_also_uncertain(self):
        """A transport timeout outside ldap3's exception hierarchy still leaves add outcome unknown."""
        connection = MagicMock()
        connection.add.side_effect = TimeoutError('socket timeout after write')

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Timeout User,OU=Users,DC=example,DC=test',
                'login_name': 'timeout.user', 'display_name': 'Timeout User',
            },
            password='Timeout-Password-Must-Not-Appear!',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, 'manual_intervention_required')
        self.assertEqual(result.error_message, '新增用户结果不确定，需要人工核查。')
        self.assertNotIn('socket timeout after write', repr(result))

    def test_create_user_timeout_after_confirmed_add_keeps_password_pending_stage(self):
        """Once add returned success, any password-write exception must remain safely recoverable."""
        connection = MagicMock()
        connection.add.return_value = True
        connection.modify.side_effect = TimeoutError('password response lost')

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Pending Timeout,OU=Users,DC=example,DC=test',
                'login_name': 'pending.timeout', 'display_name': 'Pending Timeout',
            },
            password='Pending-Timeout-Password!',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, 'user_created_password_pending')
        self.assertEqual(result.error_message, '用户已创建，但密码初始化失败；请提交新密码后重试。')
        self.assertNotIn('password response lost', repr(result))

    def test_create_user_false_add_without_result_code_is_uncertain(self):
        """A false return without an LDAP result cannot prove that the server rejected the add."""
        connection = MagicMock()
        connection.add.return_value = False
        connection.result = {}

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=No Result,OU=Users,DC=example,DC=test',
                'login_name': 'no.result', 'display_name': 'No Result',
            },
            password='No-Result-Password!',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, 'manual_intervention_required')
        connection.modify.assert_not_called()

    def test_create_user_false_add_with_success_code_is_uncertain(self):
        """A contradictory false return and success code cannot prove whether add committed."""
        connection = MagicMock()
        connection.add.return_value = False
        connection.result = {'result': 0}

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Contradictory Add,OU=Users,DC=example,DC=test',
                'login_name': 'contradictory.add', 'display_name': 'Contradictory Add',
            },
            password='Contradictory-Result-Password!',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.stage, 'manual_intervention_required')
        connection.modify.assert_not_called()

    def test_create_user_existing_without_recovery_stage_never_resets_password(self):
        """An ordinary object-exists result must not be treated as proof that this task created it."""
        connection = MagicMock()
        connection.add.return_value = False
        connection.result = {'result': 68}

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'account', 'create_user', None,
            {
                'user_dn': 'CN=Existing User,OU=Users,DC=example,DC=test',
                'login_name': 'existing.user', 'display_name': 'Existing User',
            },
            password='Must-Never-Take-Over-Existing-User!',
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_message, '目录对象已存在。')
        self.assertEqual(result.stage, '')
        connection.search.assert_not_called()
        connection.modify.assert_not_called()

    def test_move_ou_returns_the_new_stable_distinguished_name(self):
        """Without the post-move DN, the local mirror cannot make the next action addressable."""
        connection = MagicMock()
        connection.modify_dn.return_value = True
        connection.result = {'result': 0}

        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'computer', 'move_ou',
            'CN=PC-01,OU=Old,DC=example,DC=test',
            {'destination_dn': 'OU=Managed,DC=example,DC=test'},
        )

        self.assertTrue(result.success)
        self.assertEqual(result.details, {
            'distinguished_name': 'CN=PC-01,OU=Managed,DC=example,DC=test',
            'ou': 'OU=Managed,DC=example,DC=test',
        })

    def test_create_user_rejects_non_cn_or_multivalued_first_rdn_before_connecting(self):
        """Treating any first DN component as cn would accept ambiguous naming identities."""
        factory = MagicMock()
        client = DomainClient(self.config, connection_factory=factory)

        with self.assertRaises(ValidationError):
            client.execute(
                'account', 'create_user', None,
                {
                    'user_dn': 'UID=42+CN=Alice,OU=Users,DC=example,DC=test',
                    'login_name': 'alice', 'display_name': 'Alice',
                },
                password='In-Memory-Only-2948!',
            )

        factory.assert_not_called()

    def test_connection_and_unbind_failures_return_only_sanitized_results(self):
        """Letting LDAP exceptions escape would put raw server details into higher-level logs."""
        connect_failure = DomainClient(
            self.config,
            connection_factory=MagicMock(side_effect=LDAPException('raw bind detail')),
        ).execute(
            'computer', 'enable',
            'CN=PC-001,OU=Workstations,DC=example,DC=test', {},
        )
        self.assertEqual(connect_failure.error_message, 'LDAP 连接或操作失败。')
        self.assertNotIn('raw bind detail', connect_failure.error_message)

        unexpected_connect_failure = DomainClient(
            self.config,
            connection_factory=MagicMock(side_effect=RuntimeError('raw TLS detail')),
        ).execute(
            'computer', 'enable',
            'CN=PC-001,OU=Workstations,DC=example,DC=test', {},
        )
        self.assertEqual(unexpected_connect_failure.error_message, 'LDAP 连接或操作失败。')
        self.assertNotIn('raw TLS detail', unexpected_connect_failure.error_message)

        connection = MagicMock()
        connection.modify.return_value = False
        connection.result = {'result': 50, 'message': 'raw cleanup detail'}
        connection.unbind.side_effect = LDAPException('raw cleanup detail')
        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'computer', 'add_group',
            'CN=PC-001,OU=Workstations,DC=example,DC=test',
            {'group_dn': 'CN=Operators,OU=Groups,DC=example,DC=test'},
        )
        self.assertEqual(result.error_message, 'LDAP 权限不足。')

    def test_uac_assertion_retry_preserves_external_bit_after_conflict(self):
        """A stale unconditional UAC replace would erase a flag added by another writer."""
        connection = UacConflictConnection(current=0x0002)
        result = DomainClient(
            self.config, connection_factory=lambda: connection,
        ).execute(
            'computer', 'enable',
            'CN=PC-001,OU=Workstations,DC=example,DC=test', {},
        )

        self.assertTrue(result.success)
        self.assertEqual(connection.current, 0x400000)
        self.assertEqual(connection.search_count, 2)
        self.assertEqual(connection.unbind_count, 1)
        assertion_control = connection.controls[0][0]
        self.assertEqual(assertion_control[:2], ('1.3.6.1.1.12', True))
        decoded, remainder = decoder.decode(assertion_control[2], asn1Spec=Filter())
        self.assertEqual(bytes(remainder), b'')
        self.assertIn('userAccountControl', str(decoded))
        self.assertIn('2', str(decoded))

    def test_uac_password_flag_retries_and_exhaustion_returns_fixed_error(self):
        """Retrying forever or returning raw assertion errors would hide a write conflict."""
        retry_connection = UacConflictConnection(current=0, conflicts=1)
        result = DomainClient(
            self.config, connection_factory=lambda: retry_connection,
        ).execute(
            'account', 'password_never_expires',
            'CN=Alice,OU=Users,DC=example,DC=test', {'enabled': True},
        )
        self.assertTrue(result.success)
        self.assertEqual(retry_connection.current, 0x410000)

        exhausted_connection = UacConflictConnection(current=0, conflicts=3)
        exhausted = DomainClient(
            self.config, connection_factory=lambda: exhausted_connection,
        ).execute(
            'computer', 'disable',
            'CN=PC-001,OU=Workstations,DC=example,DC=test', {},
        )
        self.assertFalse(exhausted.success)
        self.assertEqual(exhausted.error_message, '目录对象状态发生并发变化，请重试。')
        self.assertEqual(exhausted_connection.search_count, 3)

    def test_connect_preserves_ports_and_common_bind_name_forms(self):
        """A connection refactor must not alter saved LDAP ports or recognized bind identities."""
        self.assertEqual(DomainClient(self.config).config.port, ['636'])
        self.assertEqual(
            effective_bind_username(SimpleNamespace(
                bind_username='sync', base_dn='DC=example,DC=test',
            )),
            'sync@example.test',
        )
        self.assertEqual(
            effective_bind_username(SimpleNamespace(
                bind_username='CN=Sync,OU=Svc,DC=example,DC=test',
                base_dn='DC=example,DC=test',
            )),
            'CN=Sync,OU=Svc,DC=example,DC=test',
        )
        self.assertEqual(
            effective_bind_username(SimpleNamespace(
                bind_username='EXAMPLE\\sync', base_dn='DC=example,DC=test',
            )),
            'EXAMPLE\\sync',
        )

    @patch('ldap3.Connection')
    @patch('ldap3.Server')
    @patch('ldap3.Tls')
    def test_connect_preserves_port_ssl_matrix_without_inferring_security_from_port(self, tls, server, _connection):
        """Treating 636 as secure or 389 as insecure would silently change saved connection behavior."""
        plain = SimpleNamespace(**{**self.config.__dict__, 'port': 389, 'use_ssl': False})
        tls_on_389 = SimpleNamespace(**{**self.config.__dict__, 'port': 389, 'use_ssl': True})

        DomainClient(plain).connect()
        DomainClient(tls_on_389).connect()

        self.assertEqual(server.call_args_list[0].kwargs['port'], 389)
        self.assertFalse(server.call_args_list[0].kwargs['use_ssl'])
        self.assertIsNone(server.call_args_list[0].kwargs['tls'])
        self.assertEqual(server.call_args_list[1].kwargs['port'], 389)
        self.assertTrue(server.call_args_list[1].kwargs['use_ssl'])
        self.assertIs(server.call_args_list[1].kwargs['tls'], tls.return_value)

    def test_group_failure_maps_ldap_result_to_non_sensitive_chinese_error(self):
        """Returning the raw LDAP description would leak backend details to operators."""
        connection = MagicMock()
        connection.modify.return_value = False
        connection.result = {
            'result': 50,
            'description': 'insufficientAccessRights',
            'message': 'CN=Sensitive Backend Detail',
        }
        client = DomainClient(self.config, connection_factory=lambda: connection)

        result = client.execute(
            'computer', 'add_group',
            'CN=PC-001,OU=Workstations,DC=example,DC=test',
            {'group_dn': 'CN=Operators,OU=Groups,DC=example,DC=test'},
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_message, 'LDAP 权限不足。')
        self.assertNotIn('Sensitive Backend Detail', result.error_message)
        connection.unbind.assert_called_once_with()

    def test_password_reset_uses_unicode_pwd_and_never_returns_password(self):
        """Using a plain LDAP password attribute would reset the wrong attribute or expose it."""
        connection = MagicMock()
        connection.modify.return_value = True
        connection.result = {'result': 0}
        client = DomainClient(self.config, connection_factory=lambda: connection)

        result = client.execute(
            'account', 'reset_password',
            'CN=Alice,OU=Users,DC=example,DC=test', {},
            password='In-Memory-Only-2948!',
        )

        self.assertTrue(result.success)
        modifications = connection.modify.call_args.args[1]
        self.assertEqual(
            modifications['unicodePwd'][0][1][0],
            '"In-Memory-Only-2948!"'.encode('utf-16-le'),
        )
        self.assertNotIn('In-Memory-Only-2948!', result.error_message)

    @patch('net.domain.sync.DomainClient')
    def test_existing_sync_connect_hook_reuses_domain_client(self, client_class):
        """Duplicating connection construction would let sync drift from the tested LDAPS behavior."""
        expected_connection = MagicMock()
        client_class.return_value.connect.return_value = expected_connection

        actual_connection = ad_sync._connect(self.config)

        self.assertIs(actual_connection, expected_connection)
        client_class.assert_called_once_with(self.config)
        client_class.return_value.connect.assert_called_once_with()
