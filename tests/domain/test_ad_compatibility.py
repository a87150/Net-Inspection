from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings
from ldap3 import NTLM, SIMPLE
from ldap3.core.exceptions import (
    LDAPUnavailableCriticalExtensionResult, LDAPInvalidCredentialsResult,
    LDAPInsufficientAccessRightsResult,
)

from net.domain.client import DomainClient
from net.domain.sync import _filetime_to_date


class ADConnection:
    """An AD-like boundary: reject RFC4528, allow ordinary attribute changes."""

    def __init__(self, value=514, *, reject_by_exception=False, apply=True):
        self.value = value
        self.reject_by_exception = reject_by_exception
        self.apply = apply
        self.result = {'result': 0}
        self.entries = []
        self.writes = []

    def search(self, *_args, **_kwargs):
        self.entries = [SimpleNamespace(userAccountControl=SimpleNamespace(value=[self.value]))]
        self.result = {'result': 0}
        return True

    def modify(self, dn, changes, controls=None):
        self.writes.append((changes, controls))
        if controls:
            self.result = {'result': 12}
            # Simulate another administrator changing an unrelated flag meanwhile.
            self.value |= 0x10000
            if self.reject_by_exception:
                raise LDAPUnavailableCriticalExtensionResult(result=12)
            return False
        if self.apply:
            self.value = changes['userAccountControl'][0][1][0]
        self.result = {'result': 0}
        return True

    def unbind(self):
        pass


class ADCompatibilityTests(SimpleTestCase):
    def setUp(self):
        self.config = SimpleNamespace(host='dc.example.test', port=636, use_ssl=True,
                                      bind_username='reader@example.test', bind_password='secret',
                                      base_dn='DC=example,DC=test')
        self.dn = 'CN=Alice,OU=Users,DC=example,DC=test'

    def execute(self, connection, action='enable', parameters=None, object_type='account'):
        return DomainClient(self.config, connection_factory=lambda: connection).execute(
            object_type, action, self.dn, parameters or {})

    def test_ad_rejecting_assertion_still_enables_and_preserves_latest_flags(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                connection = ADConnection(reject_by_exception=raises)
                result = self.execute(connection)
                self.assertTrue(result.success, result.error_message)
                self.assertEqual(connection.value, 66048)
                self.assertEqual(len(connection.writes), 2)
                self.assertIsNone(connection.writes[-1][1])

    def test_computer_disable_and_password_flag_use_ad_compatible_write(self):
        for object_type, action, params, initial, expected in (
            ('computer', 'disable', {}, 4096, 69634),
            ('account', 'password_never_expires', {'enabled': False}, 66048, 512),
        ):
            with self.subTest(action=action):
                connection = ADConnection(initial)
                result = self.execute(connection, action, params, object_type)
                self.assertTrue(result.success, result.error_message)
                self.assertEqual(connection.value, expected)

    def test_ad_readback_mismatch_is_not_reported_as_success(self):
        result = self.execute(ADConnection(apply=False))
        self.assertFalse(result.success)
        self.assertIn('核对', result.error_message)

    def test_empty_successful_search_must_not_report_operation_success(self):
        connection = MagicMock()
        connection.search.return_value = False
        connection.entries = []
        connection.result = {'result': 0}
        result = self.execute(connection)
        self.assertFalse(result.success)
        connection.modify.assert_not_called()

    def test_policy_error_is_explained_without_server_diagnostics(self):
        connection = MagicMock()
        connection.modify.return_value = False
        connection.result = {'result': 19, 'message': 'secret server diagnostics'}
        result = DomainClient(
            self.config, connection_factory=lambda: connection).execute(
                'account', 'reset_password', self.dn, {}, password='not-for-logs')
        self.assertFalse(result.success)
        self.assertIn('策略', result.error_message)
        self.assertNotIn('secret', repr(result))

    def test_domain_qualified_username_selects_ntlm_without_retrying_credentials(self):
        for username, auth in ((r'EXAMPLE\admin', NTLM), ('admin@example.test', SIMPLE)):
            with self.subTest(username=username), patch('ldap3.Server'), patch('ldap3.Connection') as factory:
                self.config.bind_username = username
                DomainClient(self.config).connect()
                self.assertEqual(factory.call_args.kwargs.get('authentication'), auth)
                self.assertEqual(factory.call_count, 1)

    def test_formatted_ldap_dates_and_overflow_do_not_break_sync(self):
        date = datetime(2026, 1, 2, tzinfo=timezone.utc)
        for value, expected in ((date, date.date()), ([date], date.date()),
                                (9223372036854775807, None), (0, None)):
            with self.subTest(value=value):
                self.assertEqual(_filetime_to_date(value), expected)

    @override_settings(TIME_ZONE='Asia/Shanghai')
    def test_sync_dates_use_local_day_for_inactivity_cutoff(self):
        moment = datetime(2026, 7, 9, 17, tzinfo=timezone.utc)
        epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
        filetime = int((moment - epoch).total_seconds()) * 10000000
        for value in (moment, [moment], filetime):
            with self.subTest(value=value):
                self.assertEqual(_filetime_to_date(value), datetime(2026, 7, 10).date())

    def test_ntlm_crypto_dependency_available_without_contacting_server(self):
        from ldap3.utils.ntlm import NtlmClient
        digest = NtlmClient(domain='Domain', user_name='User', password='Password').ntowf_v2()
        self.assertEqual(digest.hex(), '0c868a403bfd7a93a3001ef22ef02e3f')

    def test_permission_failure_must_not_fall_back_to_unconditional_write(self):
        connection = MagicMock()
        connection.search.return_value = True
        connection.entries = [SimpleNamespace(userAccountControl=SimpleNamespace(value=514))]
        connection.modify.return_value = False
        connection.result = {'result': 50}
        result = self.execute(connection)
        self.assertFalse(result.success)
        self.assertIn('权限', result.error_message)
        self.assertEqual(connection.modify.call_count, 1)

    def test_ldap_exceptions_keep_safe_actionable_failure_category(self):
        for code, exception, keyword in (
            (49, LDAPInvalidCredentialsResult, '认证'),
            (50, LDAPInsufficientAccessRightsResult, '权限'),
        ):
            with self.subTest(code=code):
                connection = MagicMock()
                connection.modify.side_effect = exception(result=code, message='private-detail')
                result = self.execute(connection, 'unlock')
                self.assertFalse(result.success)
                self.assertIn(keyword, result.error_message)
                self.assertNotIn('private-detail', repr(result))
