from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from net.models import ComputerLogFile, ComputerAnalysisProfile, TaskRun
from net.devices.pc.recovery import recover_missing_timestamps


class LegacyRecoveryTests(TestCase):
    def setUp(self):
        self.profile = ComputerAnalysisProfile.objects.create(name='repair', analysis_items=['resource'])
        self.log = ComputerLogFile.objects.create(content_hash='a'*64, import_status='failed',
            parse_error='日志时间缺失或格式无效。', modified_at=timezone.now(),
            payload={'系统信息概览': {'计算机名': 'repair-pc'}}, source_path='original.json')

    def test_repair_preserves_evidence_and_creates_analysis_task(self):
        original = (self.log.payload, self.log.content_hash, self.log.modified_at)
        task = recover_missing_timestamps([self.log.pk], self.profile)
        self.log.refresh_from_db()
        self.assertEqual(self.log.import_status, 'imported')
        self.assertEqual((self.log.payload, self.log.content_hash, self.log.modified_at), original)
        self.assertEqual(task.task_type, 'computer_analysis')
        self.assertEqual(task.target_runs.get().target_id, str(self.log.pk))

    def test_unrelated_failure_rolls_back_whole_repair(self):
        other = ComputerLogFile.objects.create(content_hash='b'*64, import_status='failed',
            parse_error='invalid JSON', modified_at=timezone.now(), source_path='bad.json')
        with self.assertRaises(ValidationError):
            recover_missing_timestamps([self.log.pk, other.pk], self.profile)
        self.log.refresh_from_db()
        self.assertEqual(self.log.import_status, 'failed')
        self.assertFalse(TaskRun.objects.exists())
