from django.test import TestCase

from tests.devices.pc.test_remote_rules import RemoteRuleFixture


class SeverityTests(RemoteRuleFixture, TestCase):
    def test_missing_optional_identity_is_notice_not_failure(self):
        self.payloads['windows']['系统信息概览'].pop('当前登录用户工号', None)
        result = self.analyze(['identity_match'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.exceptions[0]['severity'], 'info')
        self.assertFalse(result.errors.exists())

    def test_real_problem_does_not_mean_execution_failed(self):
        self.payloads['windows']['当前与域服务器通讯情况'] = '失败'
        result = self.analyze(['domain_trust'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.exceptions[0]['severity'], 'critical')
        self.assertEqual(result.errors.count(), 1)

    def test_optional_temperature_is_notice(self):
        result = self.analyze(['cpu_health'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.exceptions[0]['severity'], 'info')

    def test_resource_threshold_is_warning(self):
        self.payloads['windows']['计算机硬件资源情况']['当前CPU占用率'] = '99%'
        result = self.analyze(['resource'], rules={'cpu_max_percent': 90})
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.exceptions[0]['severity'], 'warning')

    def test_unknown_does_not_recover_real_alert_and_critical_counts_as_abnormal(self):
        from net.models import ComputerAnalysisProfile, AlertEvent, TaskRun
        from net.inspections.queue import enqueue_task, claim_next_task, finish_task
        from net.devices.pc.executor import execute_computer_target
        from net.inspections.task_summary import summarize_task
        from net.models import People
        People.objects.create(employee_id='tester', name='Test User')
        profile = ComputerAnalysisProfile.objects.create(name='severity pipeline', analysis_items=['domain_trust'])
        self.payloads['windows']['当前与域服务器通讯情况'] = '失败'
        log = self.analyze(['domain_trust']).log_file
        def run():
            task = enqueue_task(profile, [log.pk], 'manual')
            claim_next_task('severity-worker', 60)
            execute_computer_target(task.target_runs.get(), worker_id='severity-worker')
            finish_task(task.pk, 'severity-worker')
            task.refresh_from_db()
            self.assertEqual(task.status, TaskRun.Status.SUCCESS)
            return task
        task = run()
        self.assertEqual(summarize_task(task)['abnormal'], 1)
        self.assertTrue(task.alert_events.filter(event_type='abnormal').exists())
        payload = log.payload
        payload['当前与域服务器通讯情况'] = '未知'
        log.payload = payload
        log.save()
        task = run()
        self.assertFalse(task.alert_events.exists())
        self.assertEqual(summarize_task(task)['normal'], 1)
        payload = log.payload
        payload['当前与域服务器通讯情况'] = '正常'
        log.payload = payload
        log.save()
        task = run()
        self.assertTrue(task.alert_events.filter(event_type='recovery').exists())
