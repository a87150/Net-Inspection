from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from tests.auth import login_admin
from net.models import Computer, ComputerAnalysisProfile, ComputerLogFile, TaskRun


class BulkLogAnalysisTests(TestCase):
    def setUp(self):
        login_admin(self.client, username='bulk-user')
        self.profile = ComputerAnalysisProfile.objects.create(name='bulk', analysis_items=['resource'])

    def log(self, computer, stamp, retained=True, platform='windows'):
        return ComputerLogFile.objects.create(computer=computer, collected_date=stamp.date(), collected_at=stamp,
            content_hash=f'{ComputerLogFile.objects.count()+1:064x}', retained=retained, platform=platform,
            payload={'系统信息概览': {'计算机名': computer.computer_name}})

    def test_filtered_scope_analyzes_latest_log_for_each_matching_pc(self):
        first, second, other = [Computer.objects.create(computer_name=name) for name in ('match-a', 'match-b', 'other')]
        self.log(first, timezone.now() - timedelta(hours=2))
        latest = self.log(first, timezone.now())
        second_log = self.log(second, timezone.now())
        self.log(other, timezone.now(), platform='macos')
        response = self.client.post(reverse('computer_logs_analyze_bulk') + '?filter_platform=windows', {'mode': 'filtered', 'profile_id': self.profile.pk, 'selected_items': ['resource']})
        self.assertEqual(response.status_code, 302)
        task = TaskRun.objects.get(task_type='computer_analysis')
        self.assertEqual(set(task.target_runs.values_list('target_id', flat=True)), {str(latest.pk), str(second_log.pk)})

    def test_list_offers_only_filtered_analysis(self):
        self.assertContains(self.client.get(reverse('computer_log_list') + '?bulk_mode=filtered'), '分析筛选结果')
        self.assertNotContains(self.client.get(reverse('computer_log_list')), '分析最新批次')
