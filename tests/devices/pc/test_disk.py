from io import StringIO
from django.test import TestCase
from django.core.management import call_command
from net.devices.pc.disk import total_gib
from net.devices.pc.snapshot import extract_computer_snapshot
from tests.devices.pc.test_remote_rules import RemoteRuleFixture
from net.models import ComputerAnalysisProfile, IssueSeverityPolicy
from net.inspections.queue import enqueue_task


class DiskTests(RemoteRuleFixture, TestCase):
    def setUp(self):
        super().setUp()
        for key in ('磁盘总量', '磁盘总容量', 'disk_total_gb'):
            self.payloads['windows']['计算机硬件资源情况'].pop(key, None)

    def test_legacy_capacity_without_free_space_is_not_healthy_usage(self):
        payload = self.payloads['windows']
        payload['BitLocker状态'] = {'磁盘卷信息': [{'卷': 'C', '大小': '476.31 GB', '已加密百分比': '100.0%'}]}
        from tests.devices.pc.helpers import import_payload
        import_payload(payload)
        result = self.analyze(['disk'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.result_level, 'info')
        result.computer.refresh_from_db()
        self.assertEqual(str(result.computer.disk_total_gb), '476.31')
        self.assertEqual(result.exceptions[0]['data_state'], 'unknown')

    def test_threshold_and_grade_and_unselected_scope(self):
        self.payloads['windows']['磁盘空间情况'] = [{'device': 'C:', 'total_bytes': 1000, 'free_bytes': 50}]
        result = self.analyze(['disk'], rules={'disk_max_percent': 90, 'issue_severity_overrides': {'disk': 'critical'}})
        self.assertEqual(result.result_level, 'critical')
        self.assertIn('95.00%', result.exceptions[0]['详细问题'])
        self.assertFalse(self.analyze(['disk'], rules={'disk_max_percent': 95}).exceptions)
        self.assertFalse(any(x['analysis_item'] == 'disk' for x in self.analyze(['resource']).exceptions))
        profile = ComputerAnalysisProfile.objects.create(name='disk', analysis_items=['disk'], disk_max_percent=80)
        IssueSeverityPolicy.objects.create(project='computers', thresholds={'disk_max_percent': 92})
        result.log_file.payload = self.payloads['windows']
        result.log_file.save(update_fields=['payload'])
        task = enqueue_task(profile, [result.log_file_id], 'manual')
        self.assertEqual(task.profile_snapshot['disk_max_percent'], 92)
        profile.disk_max_percent = 99
        profile.save()
        task.refresh_from_db()
        self.assertEqual(task.profile_snapshot['disk_max_percent'], 92)
        from net.inspections.queue import claim_next_task
        from net.devices.pc.executor import execute_computer_target
        from net.models import ComputerAnalysis
        claim_next_task('disk-test', 60)
        execute_computer_target(task.target_runs.get(), worker_id='disk-test')
        saved = ComputerAnalysis.objects.get(task_target=task.target_runs.get())
        self.assertEqual(saved.details['disk']['max_percent'], 92)
        self.assertTrue(any(x['analysis_item'] == 'disk' for x in saved.exceptions))

    def test_invalid_or_duplicate_volumes_do_not_invent_capacity(self):
        for rows in ([{'卷':'C','大小':'100 GB'}, {'卷':'C:','大小':'100 GB'}],
                     [{'卷':'C','大小':'未知'}], [], [{'卷':'C','大小':'NaN GB'}]):
            self.assertIsNone(total_gib({'BitLocker状态': {'磁盘卷信息': rows}}))
        self.assertEqual(extract_computer_snapshot({}, [], {'磁盘总量':'1,024.00GB'})['disk_total_gb'], 1024)

    def test_backfill_only_empty_capacity_and_dry_run(self):
        self.payloads['windows']['BitLocker状态'] = {'磁盘卷信息': [{'卷':'C', '大小':'100 GB'}]}
        analysis = self.analyze(['disk'])
        computer = analysis.computer
        computer.disk_total_gb = None
        computer.save(update_fields=['disk_total_gb'])
        call_command('backfill_pc_disk_capacity', stdout=StringIO())
        computer.refresh_from_db()
        self.assertIsNone(computer.disk_total_gb)
        call_command('backfill_pc_disk_capacity', apply=True, stdout=StringIO())
        computer.refresh_from_db()
        self.assertEqual(computer.disk_total_gb, 100)
        computer.disk_total_gb = 200
        computer.save(update_fields=['disk_total_gb'])
        call_command('backfill_pc_disk_capacity', apply=True, stdout=StringIO())
        computer.refresh_from_db()
        self.assertEqual(computer.disk_total_gb, 200)
