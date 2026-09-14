from tests import response_body

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from tests.auth import login_reader
from tests.devices.pc.test_remote_rules import RemoteRuleFixture


class IssueSettingsTests(RemoteRuleFixture, TestCase):
    def test_override_is_frozen_and_category_is_recorded(self):
        from net.devices.pc.severity import grade_issue
        issue = grade_issue({'analysis_item': 'cpu_health', '问题类型': 'CPU温度问题'},
                            overrides={'cpu_health': 'info'})
        self.assertEqual(issue['severity'], 'info')
        self.assertEqual(issue['category'], '硬件')
        self.assertEqual(grade_issue(issue)['severity'], 'info')

    def test_admin_can_save_but_member_cannot_and_invalid_level_is_rejected(self):
        from net.models import IssueSeverityPolicy
        user = get_user_model().objects.create_user(username='settings-member')
        self.client.force_login(user)
        url = reverse('issue_severity_settings')
        self.assertEqual(self.client.post(url, {'rule_software': 'critical'}).status_code, 403)
        self.assertFalse(IssueSeverityPolicy.objects.exists())
        user.is_staff = True
        user.save()
        self.assertEqual(self.client.post(url, {'rule_software': 'invalid'}).status_code, 400)
        response = self.client.post(url, {'rule_software': 'critical', 'missing_cpu_health': 'info'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '已保存')
        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(IssueSeverityPolicy.objects.get(pk=1).overrides['software'], 'critical')

    def test_analysis_applies_override_before_errors_and_alerts(self):
        self.payloads['windows']['当前与域服务器通讯情况'] = '失败'
        result = self.analyze(['domain_trust'], rules={'issue_severity_overrides': {'domain_trust': 'info'}})
        self.assertEqual(result.result_level, 'info')
        self.assertFalse(result.errors.exists())

    def test_history_task_uses_same_result_table_and_scoped_export(self):
        login_reader(self.client)
        from net.models import ComputerAnalysisProfile
        from net.inspections.queue import enqueue_task
        from net.devices.pc.analysis import analyze_log
        profile = ComputerAnalysisProfile.objects.create(name='history-table', analysis_items=['cpu_health'])
        analysis = self.analyze(['cpu_health'])
        task = enqueue_task(profile, [analysis.log_file_id], 'manual')
        analyze_log(analysis.log_file, ['cpu_health'], task_target=task.target_runs.get())
        newer_profile = ComputerAnalysisProfile.objects.create(name='newer-table', analysis_items=['patches'])
        newer_task = enqueue_task(newer_profile, [analysis.log_file_id], 'manual')
        analyze_log(analysis.log_file, ['patches'], task_target=newer_task.target_runs.get())
        response = self.client.get(reverse('task_detail', args=[task.pk]), {'filter_problem_types': '硬件'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['table_definition'].key, 'computer_inspections')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertTemplateUsed(response, 'inspections/results_table.html')
        exported = self.client.get(reverse('table_export', args=['computer_inspections']), {'task': str(task.pk)})
        self.assertEqual(exported.status_code, 200)
        self.assertIn('硬件', response_body(exported).decode('utf-8-sig'))
        self.assertNotIn('系统更新', response_body(exported).decode('utf-8-sig'))
        invalid = self.client.get(reverse('table_export', args=['computer_inspections']), {'task': 'invalid'})
        self.assertEqual(invalid.status_code, 404)

    def test_policy_is_frozen_before_execution(self):
        from net.models import IssueSeverityPolicy, ComputerAnalysisProfile
        from net.inspections.queue import enqueue_task, claim_next_task
        from net.devices.pc.executor import execute_computer_target
        from net.models import People
        People.objects.create(employee_id='tester', name='Test User')
        policy = IssueSeverityPolicy.objects.create(overrides={'domain_trust': 'info'})
        profile = ComputerAnalysisProfile.objects.create(name='frozen-policy', analysis_items=['domain_trust'])
        self.payloads['windows']['当前与域服务器通讯情况'] = '失败'
        log = self.analyze(['domain_trust']).log_file
        task = enqueue_task(profile, [log.pk], 'manual')
        policy.overrides = {'domain_trust': 'critical'}
        policy.save()
        claim_next_task('frozen-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='frozen-worker')
        target = task.target_runs.get()
        from net.inspections.result_storage import expanded_result_snapshot
        self.assertEqual(expanded_result_snapshot(target)['exceptions'][0]['severity'], 'info')
        self.assertFalse(task.alert_events.exists())

    def test_device_inspection_level_and_task_table_use_frozen_policy(self):
        login_reader(self.client)
        from net.models import IssueSeverityPolicy, InspectionProfile, Server
        from net.inspections.queue import enqueue_task, claim_next_task
        from net.inspections.executor import persist_execution_failure, _begin_target
        from index.inspections.records import _infrastructure_records
        IssueSeverityPolicy.objects.create(project='servers', overrides={'inspection_collection': 'warning'})
        device = Server.objects.create(name='offline-test', ip='192.0.2.4', server_type='linux')
        profile = InspectionProfile.objects.create(name='device-policy', device_type='server', selected_items=['cpu'])
        task = enqueue_task(profile, [device.pk], 'manual')
        claim_next_task('device-worker', 60)
        target = _begin_target(task.target_runs.get().pk, 'device-worker')
        persist_execution_failure(target, worker_id='device-worker', error='offline')
        row = _infrastructure_records('servers', task=task)[0]
        self.assertEqual(row['result_level'], 'warning')
        self.assertEqual(row['problem_types'], '采集连接')
        self.assertEqual(task.alert_events.get().severity, 'warning')
        response = self.client.get(reverse('task_detail', args=[task.pk]))
        self.assertEqual(response.context['table_definition'].key, 'inspection_records')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
