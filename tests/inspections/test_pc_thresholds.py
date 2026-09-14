from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from net.models import People, IssueSeverityPolicy, ComputerAnalysisProfile
from net.inspections.issues import PC_THRESHOLD_FIELDS
from net.inspections.queue import enqueue_task, claim_next_task, finish_task
from net.devices.pc.executor import execute_computer_target
from net.inspections.result_storage import expanded_result_snapshot
from tests.auth import login_admin, login_reader
from tests.devices.pc.test_remote_rules import RemoteRuleFixture


class PCThresholdTests(RemoteRuleFixture, TestCase):
    def setUp(self):
        super().setUp()
        login_admin(self.client)
        self.url = reverse('issue_severity_settings') + '?project=computers'

    def test_form_has_multiple_resource_and_defender_thresholds_with_units(self):
        page = self.client.get(self.url)
        for key in PC_THRESHOLD_FIELDS:
            self.assertContains(page, f'name="threshold_{key}"')
        self.assertContains(page, '小时')
        self.assertContains(page, '病毒库最大间隔（天）')
        self.assertNotContains(page, 'name="threshold_traffic"')

    def test_invalid_values_and_reader_cannot_change_policy(self):
        for value in ('0', '-1', '101', '12.5', 'NaN', 'inf'):
            self.assertEqual(self.client.post(self.url, {'threshold_cpu_max_percent': value}).status_code, 400)
        self.assertFalse(IssueSeverityPolicy.objects.exists())
        for value in (True, 1.5, float('nan'), 101):
            with self.assertRaises(ValidationError):
                IssueSeverityPolicy(project='computers', thresholds={'cpu_max_percent': value}).full_clean()
        login_reader(self.client)
        self.assertEqual(self.client.post(self.url, {'threshold_cpu_max_percent': '70'}).status_code, 403)
        self.assertFalse(IssueSeverityPolicy.objects.exists())

    def test_policy_threshold_reaches_worker_and_is_frozen_then_can_be_cleared(self):
        People.objects.create(employee_id='tester', name='Test User')
        self.payloads['windows']['计算机硬件资源情况']['当前CPU占用率'] = '80%'
        self.payloads['windows']['计算机硬件资源情况']['当前内存占用率'] = '20%'
        log = self.analyze(['resource']).log_file
        profile = ComputerAnalysisProfile.objects.create(name='threshold', analysis_items=['resource'], cpu_max_percent=90)
        posted = {'threshold_disk_max_percent': '90', 'threshold_cpu_max_percent': '70', 'threshold_memory_max_percent': '85',
                  'threshold_cpu_temperature_max_celsius': '80', 'threshold_uptime_max_hours': '240',
                  'threshold_patch_max_days': '120', 'threshold_defender_update_max_days': '14',
                  'threshold_defender_scan_max_days': '21', 'rule_resource': 'critical'}
        self.assertEqual(self.client.post(self.url, posted).status_code, 200)
        task = enqueue_task(profile, [log.pk], 'manual')
        for key in PC_THRESHOLD_FIELDS:
            self.assertEqual(task.profile_snapshot[key], int(posted['threshold_' + key]))
        policy = IssueSeverityPolicy.objects.get(project='computers')
        version = policy.updated_at.isoformat()
        self.assertEqual(self.client.post(self.url, {'updated_at': version, 'threshold_cpu_max_percent': '95',
                                                    'rule_resource': 'warning'}).status_code, 200)
        self.assertEqual(self.client.post(self.url, {'updated_at': version, 'threshold_cpu_max_percent': '99'}).status_code, 400)
        claim_next_task('threshold-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='threshold-worker')
        result = expanded_result_snapshot(task.target_runs.get())
        self.assertTrue(any(x['analysis_item'] == 'resource' and x['severity'] == 'critical' for x in result['exceptions']))
        self.assertEqual(result['details']['rules']['cpu_max_percent'], 70)
        finish_task(task.pk, 'threshold-worker')
        policy.refresh_from_db()
        clear = {'updated_at': policy.updated_at.isoformat(), **{'threshold_' + key: '' for key in PC_THRESHOLD_FIELDS}}
        self.assertEqual(self.client.post(self.url, clear).status_code, 200)
        policy.refresh_from_db()
        self.assertEqual(policy.thresholds, {})
        new_task = enqueue_task(profile, [log.pk], 'manual')
        self.assertEqual(new_task.profile_snapshot['cpu_max_percent'], 90)
        claim_next_task('threshold-worker', 60)
        execute_computer_target(new_task.target_runs.get(), worker_id='threshold-worker')
        self.assertFalse(expanded_result_snapshot(new_task.target_runs.get())['exceptions'])
