from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model


class ProjectIssueSettingsTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='project-admin', is_staff=True))

    def test_each_page_only_offers_its_own_rules(self):
        url = reverse('issue_severity_settings')
        network = self.client.get(url, {'project': 'networks'})
        self.assertContains(network, 'name="rule_cpu"')
        self.assertContains(network, 'name="missing_traffic"')
        self.assertNotContains(network, 'name="rule_defender"')
        server = self.client.get(url, {'project': 'servers'})
        self.assertContains(server, 'name="rule_services"')
        self.assertNotContains(server, 'name="rule_vlan_status"')
        security = self.client.get(url, {'project': 'monitors'})
        self.assertContains(security, 'name="rule_channel_status"')
        self.assertNotContains(security, 'name="rule_software"')

    def test_save_is_isolated_and_rejects_foreign_rules(self):
        from net.inspections.issues import policy_snapshot
        url = reverse('issue_severity_settings')
        self.client.post(url + '?project=networks', {'rule_cpu': 'critical'})
        self.client.post(url + '?project=servers', {'rule_cpu': 'warning'})
        self.assertEqual(policy_snapshot('networks')['cpu'], 'critical')
        self.assertEqual(policy_snapshot('servers')['cpu'], 'warning')
        self.assertNotIn('cpu', policy_snapshot('computers'))
        self.assertEqual(self.client.post(url + '?project=networks', {'rule_defender': 'critical'}).status_code, 400)

    def test_network_metrics_and_counter_missing_use_separate_rules(self):
        from net.inspections.device_issues import evaluate_device_issues
        issues, _ = evaluate_device_issues('networks', ['cpu', 'memory', 'temperature', 'traffic'],
            {'cpu': {'usage_percent': 98}, 'memory': {'usage_percent': 20},
             'temperature': {'values_celsius': [35]}, 'traffic': {'interfaces': [
                 {'name': 'eth0', 'data_state': 'unknown'}]}},
            reachable=True, status='success', overrides={'cpu': 'critical', 'missing.traffic': 'info'})
        self.assertEqual({issue['analysis_item']: issue['severity'] for issue in issues}, {'cpu': 'critical', 'traffic': 'info'})
        self.assertEqual(issues[0]['category'], 'CPU')

    def test_cumulative_octets_are_not_bandwidth_and_unselected_items_are_ignored(self):
        from net.inspections.device_issues import evaluate_device_issues
        issues, _ = evaluate_device_issues('networks', ['interface_status'],
            {'cpu': {'usage_percent': 99}, 'interface_status': {'interfaces': [
                 {'in_octets': 999999999999, 'out_octets': 999999999999}]}},
            reachable=True, status='success')
        self.assertEqual(issues, [])

    def test_traffic_threshold_is_independent_and_uses_utilization(self):
        from net.inspections.device_issues import evaluate_device_issues
        issues, normal = evaluate_device_issues('networks', ['traffic'],
            {'traffic': {'interfaces': [{'data_state': 'known', 'rx_mbps': 950, 'tx_mbps': 10, 'utilization_percent': 95}]}},
            reachable=True, status='success', thresholds={'traffic': 80}, overrides={'traffic': 'critical'})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['severity'], 'critical')
        self.assertEqual(issues[0]['category'], '实时接口流量')
        self.assertNotIn('traffic', normal)

    def test_setting_windows_on_record_pages_target_correct_projects(self):
        for project in ('networks', 'servers', 'monitors'):
            response = self.client.get(reverse('record_list', args=[project]))
            self.assertNotContains(response, reverse('collection_templates', args=[project]))
            self.assertNotContains(response, 'id="issueSeverityModal"')
            choices = response.context['table_state']['field_options']['problem_types']
            self.assertNotIn(('杀毒', '杀毒'), choices)
