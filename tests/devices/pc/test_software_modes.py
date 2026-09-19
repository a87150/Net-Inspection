from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from net.devices.pc.checks import check_software
from net.models import ComputerAnalysisProfile
from index.inspections.forms import ComputerAnalysisProfileConfigForm

POLICY = {
    'WHITELIST': {'basic': ['Browser', 'SharedApp']},
    'SPECIAL_WHITELIST': {'Support': ['E1']},
    'BLACKLIST': {'keywords': ['Game', 'SharedApp']},
}

class SoftwareModeChecks(SimpleTestCase):
    def findings(self, mode, identity='E1'):
        issues = []
        payload = {'已安装软件列表': [{'软件名': name} for name in
                   ['Browser', 'Support', 'Unknown', 'Game', 'SharedApp', 'Game']]}
        check_software(payload, identity, issues, POLICY, mode=mode)
        return issues[0]['详细问题'] if issues else ''

    def test_whitelist_uses_only_allow_lists_and_deduplicates(self):
        self.assertEqual(self.findings('whitelist'), 'Game，Unknown')

    def test_whitelist_special_permission_is_per_identity(self):
        self.assertEqual(self.findings('whitelist', 'E2'), 'Game，Support，Unknown')

    def test_blacklist_does_not_flag_unlisted_or_exempt_allowlisted_matches(self):
        self.assertEqual(self.findings('blacklist'), 'Game，SharedApp')

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.findings('invalid')

    def test_legacy_tasks_keep_blacklist_precedence(self):
        self.assertEqual(self.findings('legacy'), 'Game，SharedApp，Unknown')

class SoftwareModeConfiguration(TestCase):
    def setUp(self):
        from tests.auth import login_admin
        login_admin(self.client)
        self.profile = ComputerAnalysisProfile.objects.create(name='Mode config', analysis_items=['software'])

    def data(self, mode):
        return {'profile_id': str(self.profile.pk), 'name': self.profile.name,
                'analysis_items': ['software'], 'concurrent_workers': '2', 'software_policy_mode': mode}

    def test_post_persists_mode_without_changing_policy_file(self):
        self.profile.software_policy_path = 'config/examples/software-policy.ini'
        self.profile.save()
        response = self.client.post(reverse('computer_analysis_profile_configure'), self.data('blacklist'))
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.software_policy_mode, 'blacklist')
        self.assertEqual(self.profile.software_policy_path, 'config/examples/software-policy.ini')
        form = ComputerAnalysisProfileConfigForm(instance=self.profile)
        self.assertEqual(form['software_policy_mode'].value(), 'blacklist')
        omitted = self.data('blacklist'); omitted.pop('software_policy_mode')
        self.client.post(reverse('computer_analysis_profile_configure'), omitted)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.software_policy_mode, 'blacklist')

    def test_invalid_mode_and_non_admin_cannot_change_profile(self):
        form = ComputerAnalysisProfileConfigForm(self.data('all'), instance=self.profile)
        self.assertFalse(form.is_valid())
        self.assertIn('software_policy_mode', form.errors)
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_user('mode-reader'))
        self.assertEqual(self.client.post(reverse('computer_analysis_profile_configure'), self.data('blacklist')).status_code, 403)

    def test_worker_uses_frozen_mode_and_persists_it_in_results(self):
        from net.inspections.queue import enqueue_task, claim_next_task, finish_task
        from net.devices.pc.executor import execute_computer_target
        from tests.devices.pc.helpers import import_payload
        from net.models import ComputerAnalysis
        from unittest.mock import patch
        log = import_payload({'日志时间': '2026-09-15 12:00:00',
            '系统信息概览': {'计算机名': 'MODE-PC'},
            '已安装软件列表': [{'软件名': 'UnlistedTool'}, {'软件名': 'Game'}]}).log_file
        self.profile.software_policy_mode = 'blacklist'; self.profile.save()
        with patch('net.inspections.queue.software_policy_snapshot', return_value={'content': POLICY}):
            task = enqueue_task(self.profile, [log.pk], 'manual')
        self.profile.software_policy_mode = 'whitelist'; self.profile.save()
        claim_next_task('mode-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='mode-worker')
        finish_task(task.pk, 'mode-worker')
        result = ComputerAnalysis.objects.get(task_target__task=task)
        self.assertEqual(result.details['rules']['software_policy_mode'], 'blacklist')
        issues = [v for v in result.exceptions if v['问题类型'] == '软件问题']
        self.assertEqual(issues[0]['详细问题'], 'Game')
        response = self.client.get(reverse('computer_analysis_detail', args=[result.pk]))
        self.assertContains(response, '本次软件分析模式：黑名单模式')
