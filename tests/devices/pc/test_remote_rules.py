import copy
import json
from pathlib import Path
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from net.devices.pc.analysis import analyze_log
from net.models import Computer, ComputerLogFile


class RemoteRuleFixture:
    def setUp(self):
        self.payloads = {
            platform: json.loads((Path(__file__).parent / 'fixtures' /
                                  f'terminal_log_{platform}.json').read_text(encoding='utf-8'))
            for platform in ('windows', 'macos')
        }

    def analyze(self, items, platform='windows', payload=None, rules=None):
        payload = copy.deepcopy(payload or self.payloads[platform])
        pc, _ = Computer.objects.get_or_create(computer_name=payload['系统信息概览']['计算机名'])
        log = ComputerLogFile.objects.filter(computer=pc).first()
        if log is None:
            log = ComputerLogFile.objects.create(
                computer=pc, collected_date=timezone.localdate(), platform=platform,
                source_path='fixture.json', content_hash=uuid4().hex,
                modified_at=timezone.now(), import_status='imported', payload=payload,
            )
        else:
            log.payload = payload
        return analyze_log(log, items, rules=rules)


class RemoteRuleTests(RemoteRuleFixture, TestCase):
    def test_temperature_threshold_and_failed_domain_state_are_used(self):
        payload = self.payloads['windows']
        payload['计算机硬件资源情况']['当前CPU温度'] = '90 °C'
        payload['当前与域服务器通讯情况'] = '失败'
        result = self.analyze(['domain_trust', 'cpu_health'], payload=payload,
                              rules={'cpu_temperature_max_celsius': 85})
        self.assertEqual(result.status, 'failed')
        self.assertEqual(set(result.errors.values_list('error_type', flat=True)),
                         {'域信任问题', 'CPU温度问题'})
        result = self.analyze(['cpu_health'], payload=payload,
                              rules={'cpu_temperature_max_celsius': 95})
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.details['cpu_health']['frequency_mhz'], 2400)

    def test_optional_temperature_remains_partial_and_abnormal(self):
        result = self.analyze(['cpu_health'])
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.details['cpu_health']['data_state'], 'partial')
        self.assertIsNone(result.details['cpu_health']['temperature_celsius'])

    def test_domain_states_are_distinguished(self):
        for value, state, status in [('正常', 'known', 'success'),
                                     ('失败', 'failed', 'failed'),
                                     ('检测失败', 'unknown', 'failed')]:
            with self.subTest(value=value):
                self.payloads['windows']['当前与域服务器通讯情况'] = value
                result = self.analyze(['domain_trust'])
                self.assertEqual(result.status, status)
                self.assertEqual(result.details['domain_trust']['data_state'], state)

    def test_macos_system_and_resource_are_successful(self):
        self.assertEqual(self.analyze(['system', 'resource'], 'macos').status, 'success')

    def test_windows_only_items_are_not_missing_on_macos(self):
        items = ['activation', 'bitlocker', 'defender', 'patches', 'domain',
                 'domain_trust', 'group_policy']
        result = self.analyze(items, 'macos')
        self.assertEqual(result.status, 'success')
        for item in items:
            self.assertEqual(result.details[item]['data_state'], 'not_applicable')

    def test_selected_cross_platform_missing_data_is_abnormal(self):
        for item in ['browser_extensions', 'cpu_health', 'event_findings', 'uptime']:
            with self.subTest(item=item):
                self.assertEqual(self.analyze([item], 'macos').status, 'failed')

    def test_identity_uses_login_suffix_and_does_not_trust_claimed_match(self):
        system = self.payloads['windows']['系统信息概览']
        system['当前登录用户工号'] = 'EXAMPLE\\test-pc'
        self.assertEqual(self.analyze(['identity_match']).status, 'success')
        system['当前登录用户工号'] = 'different@example.test'
        self.payloads['windows']['计算机和用户匹配情况'] = '正常'
        self.assertEqual(self.analyze(['identity_match']).status, 'failed')

    def test_browser_empty_is_known_but_null_and_malformed_are_abnormal(self):
        self.assertEqual(self.analyze(['browser_extensions']).status, 'success')
        for value in [None, {}, {'Chrome': None}, {'Chrome': [42]}]:
            self.payloads['windows']['浏览器插件情况'] = value
            self.assertEqual(self.analyze(['browser_extensions']).status, 'failed')

    def test_group_policy_preserves_unknown_user_collection(self):
        self.payloads['windows']['已应用策略']['用户策略'] = None
        result = self.analyze(['group_policy'])
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.details['group_policy']['evidence'],
                         {'计算机策略': [], '用户策略': None})
        self.payloads['windows']['已应用策略']['用户策略'] = []
        self.assertEqual(self.analyze(['group_policy']).status, 'success')

    def test_cpu_units_and_malformed_values(self):
        hardware = self.payloads['windows']['计算机硬件资源情况']
        hardware.update({'当前CPU温度': '185°F', '当前CPU频率': '2.4 GHz'})
        result = self.analyze(['cpu_health'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.details['cpu_health']['temperature_celsius'], 85)
        self.assertEqual(result.details['cpu_health']['frequency_mhz'], 2400)
        for value in ['NaN', '-2GHz', 'fast', 0]:
            hardware['当前CPU频率'] = value
            self.assertEqual(self.analyze(['cpu_health']).status, 'failed')

    def test_windows_caption_uses_build_to_apply_release_threshold(self):
        self.assertEqual(self.analyze(['system'], rules={'minimum_windows_release': '23H2'}).status, 'success')
        self.assertEqual(self.analyze(['system'], rules={'minimum_windows_release': '25H2'}).status, 'failed')

    def test_resource_units_allow_spaces_and_threshold_is_applied(self):
        self.payloads['windows']['计算机硬件资源情况']['当前CPU占用率'] = ' 12.0 % '
        self.assertEqual(self.analyze(['resource'], rules={'cpu_max_percent': 20}).status, 'success')
        result = self.analyze(['resource'], rules={'cpu_max_percent': 10})
        self.assertEqual(set(result.errors.values_list('error_type', flat=True)), {'资源使用问题'})

    def test_kms_normal_state_is_normalized(self):
        self.payloads['windows']['Windows激活信息']['描述'] = 'KMS'
        self.payloads['windows']['KMS服务器连通情况'] = '正常'
        self.assertEqual(self.analyze(['activation']).status, 'success')

    def test_snapshot_retains_fixture_bios_serial(self):
        from net.devices.pc.snapshot import extract_computer_snapshot
        payload = self.payloads['windows']
        snapshot = extract_computer_snapshot(payload['系统信息概览'], payload['网络信息'],
                                             payload['计算机硬件资源情况'])
        self.assertEqual(snapshot.get('serial_number'), 'SYNTHETIC-001')

    def test_unknown_collection_sentinels_are_not_normal_evidence(self):
        self.payloads['windows']['浏览器插件情况'] = {'Chrome': ['检测失败']}
        self.assertEqual(self.analyze(['browser_extensions']).status, 'failed')
        self.payloads['windows']['已应用策略'] = {'计算机策略': ['未知'], '用户策略': []}
        self.assertEqual(self.analyze(['group_policy']).status, 'failed')

    def test_platform_choices_filter_only_windows_checks(self):
        from net.devices.pc.analysis import analysis_items_for_platform
        items = ['system', 'domain_trust', 'resource', 'group_policy', 'cpu_health']
        self.assertEqual(analysis_items_for_platform(items, 'macos'), ['system', 'resource', 'cpu_health'])
        self.assertEqual(analysis_items_for_platform(items, 'windows'), items)

    def test_selected_new_sections_missing_on_windows_remain_abnormal(self):
        for item, field in [('browser_extensions', '浏览器插件情况'),
                            ('group_policy', '已应用策略'),
                            ('domain_trust', '当前与域服务器通讯情况'),
                            ('cpu_health', '计算机硬件资源情况')]:
            with self.subTest(item=item):
                payload = copy.deepcopy(self.payloads['windows'])
                del payload[field]
                result = self.analyze([item], payload=payload)
                self.assertEqual(result.status, 'failed')
                self.assertEqual(result.details[item]['data_state'], 'missing')
