from unittest.mock import patch
import hashlib
from html.parser import HTMLParser
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from net.inspections import issues, queue
from net.models import InspectionProfile, IssueSeverityPolicy, Server, TaskRun


class ConfigurationSnapshotTests(TestCase):
    def hidden_version(self, response):
        class Inputs(HTMLParser):
            tokens = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input' and attrs.get('name') == 'updated_at':
                    self.tokens.append(attrs)

        parser = Inputs()
        parser.tokens = []
        parser.feed(response.content.decode())
        self.assertEqual(len(parser.tokens), 1)
        self.assertEqual(parser.tokens[0]['type'], 'hidden')
        return parser.tokens[0].get('value', '')

    def test_browser_consecutive_saves_refresh_token_and_reject_old_page(self):
        self.client.force_login(get_user_model().objects.create_user(username='browser-admin', is_staff=True))
        IssueSeverityPolicy.objects.create(project='servers')
        url = reverse('issue_severity_settings') + '?project=servers'
        first = self.hidden_version(self.client.get(url))
        saved = self.client.post(url, {'updated_at': first, 'rule_cpu': 'critical', 'threshold_cpu': '72'})
        self.assertEqual(saved.status_code, 200)
        second = self.hidden_version(saved)
        self.assertNotEqual(first, second)
        saved_again = self.client.post(url, {'updated_at': second, 'rule_cpu': 'warning', 'threshold_cpu': '73'})
        self.assertEqual(saved_again.status_code, 200)
        self.assertNotEqual(second, self.hidden_version(saved_again))
        stale = self.client.post(url, {'updated_at': first, 'rule_cpu': 'info'})
        self.assertEqual(stale.status_code, 400)
        policy = IssueSeverityPolicy.objects.get(project='servers')
        self.assertEqual(policy.overrides, {'cpu': 'warning'})
        self.assertEqual(policy.thresholds, {'cpu': 73})

    def test_empty_browser_token_detects_policy_created_since_get(self):
        self.client.force_login(get_user_model().objects.create_user(username='empty-admin', is_staff=True))
        url = reverse('issue_severity_settings') + '?project=servers'
        token = self.hidden_version(self.client.get(url))
        self.assertEqual(token, '')
        saved = self.client.post(url, {'updated_at': token, 'rule_cpu': 'critical'})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(self.hidden_version(saved))
        self.assertEqual(self.client.post(url, {'updated_at': token, 'rule_cpu': 'info'}).status_code, 400)
        self.assertEqual(IssueSeverityPolicy.objects.get(project='servers').overrides, {'cpu': 'critical'})

    def test_fetch_and_children_keep_personnel_and_software_frozen(self):
        from net.models import ComputerAnalysisProfile
        from tests.devices.pc.helpers import create_log_file
        from tests.devices.pc.test_source_models import valid_smb_source
        with TemporaryDirectory() as folder:
            valid_smb_source(local_staging_directory=folder)
            profile = ComputerAnalysisProfile.objects.create(name='family', analysis_items=['software'], software_policy_path='rules.ini')
            roster = [{'id': '1', 'employee_id': 'E1', 'name': 'Original'}]
            with patch('net.devices.pc.matching.personnel_snapshot', return_value=roster) as people, patch('pathlib.Path.read_bytes', return_value=b'[WHITELIST]\nApps=One\n') as read:
                parent = queue.enqueue_computer_fetch_task(profile, 'manual')
            people.assert_called_once_with()
            read.assert_called_once_with()
            parent.refresh_from_db()
            self.assertEqual(parent.parameters_snapshot['personnel_roster'], roster)
            with patch('net.devices.pc.matching.personnel_snapshot', side_effect=AssertionError('live personnel')), patch('pathlib.Path.read_bytes', side_effect=AssertionError('live policy')):
                for index in range(2):
                    from django.utils import timezone
                    log = create_log_file(source_path=f'family-{index}.json', modified_at=timezone.now(),
                                          content_hash=hashlib.sha256(str(index).encode()).hexdigest())
                    child = queue.enqueue_task(profile, [log.pk], 'manual',
                        overrides={'parameters': {'personnel_roster': [{'name': 'replacement'}]}}, _frozen_parent=parent)
                    child.refresh_from_db()
                    self.assertEqual(child.parameters_snapshot['personnel_roster'], roster)
                    self.assertEqual(child.profile_snapshot, parent.profile_snapshot)

    def test_policy_pair_uses_one_row_read(self):
        IssueSeverityPolicy.objects.create(project='servers', overrides={'cpu': 'critical'}, thresholds={'cpu': 75})
        with self.assertNumQueries(1):
            severity, thresholds = issues.configuration_snapshot('servers')
        self.assertEqual(severity, {'cpu': 'critical'})
        self.assertEqual(thresholds['cpu'], 75)

    def test_enqueue_resolves_context_once(self):
        profile = InspectionProfile.objects.create(name='snapshot', device_type='server', selected_items=['cpu'])
        server = Server.objects.create(name='snapshot', ip='192.0.2.42', server_type='linux', os='Linux')
        with patch.object(queue, '_task_context_for_profile', wraps=queue._task_context_for_profile) as context:
            queue.enqueue_task(profile, [server.pk], TaskRun.Source.MANUAL)
        self.assertEqual(context.call_count, 1)

    def test_policy_error_does_not_expose_parser_input(self):
        with patch('pathlib.Path.read_bytes', side_effect=OSError('password=secret-value')):
            snapshot = queue.software_policy_snapshot('policy.ini')
        self.assertEqual(snapshot['sha256'], '')
        self.assertNotIn('secret-value', snapshot['error'])

    def test_policy_bytes_read_once_and_hashed(self):
        raw = b'[Required]\nApps=One, Two\n'
        with patch('pathlib.Path.read_bytes', return_value=raw) as read:
            snapshot = queue.software_policy_snapshot('policy.ini')
        read.assert_called_once_with()
        self.assertEqual(snapshot, {'content': {'Required': {'Apps': ['One', 'Two']}},
                                    'sha256': hashlib.sha256(raw).hexdigest()})

    def test_frozen_parent_never_reads_live_context(self):
        profile = InspectionProfile.objects.create(name='frozen', device_type='server', selected_items=['cpu'])
        server = Server.objects.create(name='frozen', ip='192.0.2.43', server_type='linux', os='Linux')
        parent = SimpleNamespace(selected_items_snapshot=['cpu'], profile_snapshot={'name': 'original'})
        with patch.object(queue, '_task_context_for_profile', side_effect=AssertionError('live read')):
            task = queue.enqueue_task(profile, [server.pk], TaskRun.Source.MANUAL, _frozen_parent=parent)
        self.assertEqual(task.profile_snapshot, parent.profile_snapshot)

    def test_stale_settings_rejected_and_legacy_post_supported(self):
        self.client.force_login(get_user_model().objects.create_user(username='snapshot-admin', is_staff=True))
        policy = IssueSeverityPolicy.objects.create(project='servers', thresholds={'cpu': 75})
        url = reverse('issue_severity_settings') + '?project=servers'
        version = policy.updated_at.isoformat()
        self.assertEqual(self.client.post(url, {'rule_cpu': 'critical'}).status_code, 200)
        response = self.client.post(url, {'rule_cpu': 'warning', 'updated_at': version})
        self.assertEqual(response.status_code, 400)
        policy.refresh_from_db()
        self.assertEqual(policy.overrides, {'cpu': 'critical'})
        self.assertEqual(policy.thresholds, {'cpu': 75})

    def test_concurrent_create_returns_validation_error(self):
        from index.inspections.issue_settings import SeveritySettingsForm
        self.client.force_login(get_user_model().objects.create_user(username='create-admin', is_staff=True))
        original = SeveritySettingsForm.overrides

        def concurrent_create(form):
            IssueSeverityPolicy.objects.create(project='servers', overrides={'cpu': 'critical'})
            return original(form)

        with patch.object(SeveritySettingsForm, 'overrides', concurrent_create):
            response = self.client.post(reverse('issue_severity_settings') + '?project=servers', {'rule_cpu': 'warning'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(IssueSeverityPolicy.objects.get(project='servers').overrides, {'cpu': 'critical'})

    def test_concurrent_update_cannot_overwrite_new_values(self):
        from index.inspections.issue_settings import SeveritySettingsForm
        from django.utils import timezone
        self.client.force_login(get_user_model().objects.create_user(username='update-admin', is_staff=True))
        policy = IssueSeverityPolicy.objects.create(project='servers')
        original = SeveritySettingsForm.overrides

        def concurrent_update(form):
            IssueSeverityPolicy.objects.filter(pk=policy.pk).update(overrides={'cpu': 'critical'}, updated_at=timezone.now())
            return original(form)

        with patch.object(SeveritySettingsForm, 'overrides', concurrent_update):
            response = self.client.post(reverse('issue_severity_settings') + '?project=servers', {'rule_cpu': 'warning'})
        self.assertEqual(response.status_code, 400)
        policy.refresh_from_db()
        self.assertEqual(policy.overrides, {'cpu': 'critical'})
