from django.test import TestCase
from tests.devices.pc.test_remote_rules import RemoteRuleFixture


class CollectionDiagnosticsTests(RemoteRuleFixture, TestCase):
    def test_partial_software_does_not_claim_uninstalled_or_healthy(self):
        self.payloads['windows']['已安装软件列表'] = [{'软件名': 'Available', '版本': '1'}]
        self.payloads['windows']['采集诊断'] = {'已安装软件列表': ['PermissionDenied: RegistryRead']}
        result = self.analyze(['software'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.result_level, 'info')
        self.assertEqual(result.exceptions[0]['data_state'], 'partial')
        self.assertIn('RegistryRead', result.exceptions[0]['详细问题'])
        self.assertEqual(result.log_file.payload['已安装软件列表'][0]['软件名'], 'Available')

    def test_missing_temperature_and_simple_process_names_are_supported(self):
        self.payloads['windows']['计算机硬件资源情况']['当前CPU温度'] = None
        self.payloads['windows']['当前运行进程清单'] = ['chrome', 'svchost']
        self.payloads['windows']['采集诊断'] = {'CPU温度': ['No readable CPU sensor']}
        result = self.analyze(['processes', 'cpu_health'])
        self.assertEqual(result.details['processes'], ['chrome', 'svchost'])
        self.assertIn('collection_diagnostic', result.details['cpu_health'])
        self.assertFalse(any(x['severity'] != 'info' for x in result.exceptions))

    def test_partial_software_still_persists_known_blacklist_findings(self):
        self.payloads['windows']['已安装软件列表'] = [
            {'软件名': 'Allowed Editor', '版本': '1'},
            {'软件名': 'BlockedApp', '版本': '2'},
        ]
        self.payloads['windows']['采集诊断'] = {'已安装软件列表': ['PermissionDenied: RegistryRead']}
        result = self.analyze(['software'], rules={'software_policy_snapshot': {'content': {
            'WHITELIST': {'keywords': ['Allowed']}, 'BLACKLIST': {'keywords': ['BlockedApp']},
        }}})
        self.assertEqual(result.status, 'success')
        self.assertIn(result.result_level, ('warning', 'critical'))
        findings = [item for item in result.exceptions if item['问题类型'] == '软件问题']
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['详细问题'], 'BlockedApp')
        self.assertTrue(result.errors.filter(error_type='软件问题').exists())
        self.assertTrue(any(item.get('data_state') == 'partial' for item in result.exceptions))
        self.assertEqual(result.details['software'], self.payloads['windows']['已安装软件列表'])

    def test_missing_software_remains_unknown_without_fabricated_blacklist_findings(self):
        self.payloads['windows']['已安装软件列表'] = None
        self.payloads['windows']['采集诊断'] = {'已安装软件列表': ['PermissionDenied: RegistryRead']}
        result = self.analyze(['software'], rules={'software_policy_snapshot': {'content': {
            'BLACKLIST': {'keywords': ['BlockedApp']},
        }}})
        self.assertEqual(result.result_level, 'info')
        self.assertFalse(result.errors.filter(error_type='软件问题').exists())
