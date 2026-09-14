from django.test import TestCase, override_settings

from net.models import Computer, Domain_Account, Domain_Computer, People
from tests.devices.pc.test_remote_rules import RemoteRuleFixture


class EnrichmentTests(TestCase):
    def setUp(self):
        self.pc = Computer.objects.create(computer_name='TEST051281')
        self.person = People.objects.create(employee_id='TEST051281', name='Test Person',
                                            department='Engineering')
        Domain_Account.objects.create(login_name='test051281', account_name='Test Person',
                                      ou='OU=Users,OU=Site')
        Domain_Computer.objects.create(computer_name='test051281', ou='OU=Computers,OU=Site')
        self.payload = {'系统信息概览': {'当前登录用户工号': 'EXAMPLE\\test051281'},
                        '网络信息': [{'IP地址': '192.0.2.10'}]}

    def test_matches_synchronized_models_by_normalized_identifier(self):
        from net.devices.pc.enrichment import build_pc_enrichment
        result = build_pc_enrichment(self.pc, self.payload)
        self.assertEqual(result['employee_number'], 'TEST051281')
        self.assertEqual(result['personnel_name'], 'Test Person')
        self.assertEqual(result['department'], 'Engineering')
        self.assertEqual(result['user_ou'], 'OU=Users,OU=Site')
        self.assertEqual(result['computer_ou'], 'OU=Computers,OU=Site')

    @override_settings(PC_SITE_IP_PREFIXES={'192.0': 'Broad', '192.0.2.0/24': 'Site'})
    def test_site_uses_most_specific_configured_prefix(self):
        from net.devices.pc.enrichment import build_pc_enrichment
        self.assertEqual(build_pc_enrichment(self.pc, self.payload)['site'], 'Site')
        self.payload['网络信息'][0]['IP地址'] = '192.0.20.1'
        self.assertEqual(build_pc_enrichment(self.pc, self.payload)['site'], 'Broad')

    def test_upn_and_missing_identity(self):
        from net.devices.pc.enrichment import build_pc_enrichment
        self.payload['系统信息概览']['当前登录用户工号'] = 'TEST051281@example.test'
        self.assertEqual(build_pc_enrichment(self.pc, self.payload)['employee_number'], 'TEST051281')
        self.payload['系统信息概览'] = {}
        result = build_pc_enrichment(self.pc, self.payload)
        self.assertEqual(result['personnel_match'], 'missing')
        self.assertEqual(result['employee_number'], '')

    def test_enrichment_uses_database_without_network_connections(self):
        from unittest.mock import patch
        from net.devices.pc.enrichment import build_pc_enrichment
        with patch('socket.socket', side_effect=AssertionError('Live network access is forbidden')):
            result = build_pc_enrichment(self.pc, self.payload)
        self.assertEqual(result['employee_number'], 'TEST051281')
        self.assertEqual(result['computer_ou'], 'OU=Computers,OU=Site')

    def test_ambiguous_account_does_not_select_arbitrary_ou(self):
        from net.devices.pc.enrichment import build_pc_enrichment
        Domain_Account.objects.create(login_name='test051281@example.test', account_name='Other', ou='OU=Other')
        result = build_pc_enrichment(self.pc, self.payload)
        self.assertEqual(result['domain_account_match'], 'ambiguous')
        self.assertEqual(result['user_ou'], '')

    @override_settings(PC_SITE_IP_PREFIXES={'192.0.2': 'A', '198.51.100': 'B'})
    def test_multiple_sites_remain_ambiguous_and_prefixes_use_octet_boundaries(self):
        from net.devices.pc.enrichment import build_pc_enrichment
        self.payload['网络信息'][0]['IP地址'] = '192.0.2.1;198.51.100.1'
        result = build_pc_enrichment(self.pc, self.payload)
        self.assertEqual(result['sites'], ['A', 'B'])
        self.assertEqual(result['site'], '')
        self.assertEqual(result['site_match'], 'ambiguous')
        self.payload['网络信息'][0]['IP地址'] = '192.0.20.1'
        self.assertEqual(build_pc_enrichment(self.pc, self.payload)['site'], '')

    def test_ou_falls_back_to_synchronized_distinguished_name(self):
        from net.devices.pc.enrichment import build_pc_enrichment
        Domain_Computer.objects.update(ou='', distinguished_name=r'CN=PC\, Test,OU=Computers,OU=Site,DC=example,DC=test')
        self.assertEqual(build_pc_enrichment(self.pc, self.payload)['computer_ou'], 'OU=Computers,OU=Site')


class FrozenEnrichmentTests(RemoteRuleFixture, TestCase):
    def test_analysis_freezes_enrichment_and_profile_site_rules(self):
        person = People.objects.create(employee_id='tester', name='Original')
        result = self.analyze(['resource'], rules={'site_ip_prefixes': {'192.0.2': 'Fixture Site'}})
        person.name = 'Changed'
        person.save()
        result.refresh_from_db()
        self.assertEqual(result.details['enrichment']['personnel_name'], 'Original')
        self.assertEqual(result.details['enrichment']['site'], 'Fixture Site')

    def test_detail_page_displays_frozen_personnel_and_ou(self):
        from django.template.loader import render_to_string
        People.objects.create(employee_id='tester', name='Frozen Person', department='Frozen Department')
        account = Domain_Account.objects.create(login_name='tester', account_name='Test', ou='OU=Frozen')
        result = self.analyze(['resource'])
        account.ou = 'OU=Changed'
        account.save()
        html = render_to_string('inspections/record_detail.html',
                                {'record_type': 'computer_analysis', 'analysis': result})
        self.assertIn('Frozen Person', html)
        self.assertIn('Frozen Department', html)
        self.assertIn('OU=Frozen', html)
        self.assertNotIn('OU=Changed', html)
