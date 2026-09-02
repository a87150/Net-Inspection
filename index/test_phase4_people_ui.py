"""Personnel import acceptance: real queue/preview/apply, fixture-only I/O."""

from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from index.test_phase4_people_sync import SnapshotAdapter
from net.integrations.people import DirectoryPerson
from net.models import People, PeopleSyncSource, TaskRun, TaskTargetRun
from net.tasks.queue import claim_next_task, finish_task, recover_expired_tasks


class FixtureDirectory(SnapshotAdapter):
    def __init__(self, source):
        super().__init__(source, [DirectoryPerson(
            employee_id='P4-001', name='预览员工', department='运维',
            external_user_id='fixture-user',
        )])

    def test_connection(self):
        return None


class PeopleFlowMixin:
    def setUp(self):
        super().setUp()
        self.source = PeopleSyncSource.objects.create(
            name='总部目录', source_key='hq', source_type='feishu',
            credentials={'app_id': 'private-app-id', 'app_secret': 'private-app-secret'},
        )
        self.other = PeopleSyncSource.objects.create(
            name='分部目录', source_key='branch', source_type='feishu',
            credentials={'app_id': 'branch-app', 'app_secret': 'branch-secret'},
        )
        self.factory = patch('net.tasks.executors.people.build_directory_adapter', FixtureDirectory)
        # Start only once the public entry exists: RED is missing UI behavior, not import errors.

    def enqueue(self, operation='preview', source=None, client=None):
        response = (client or self.client).post(
            f'/integrations/people/{operation}/', {'source_id': (source or self.source).pk},
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        task = TaskRun.objects.latest('created_at')
        return task, response['Location']

    def execute(self, task):
        from net.tasks.executors.people import execute_people_target
        claimed = claim_next_task('fixture-worker', 60)
        self.assertEqual(claimed.pk, task.pk)
        target = claimed.target_runs.get()
        with self.factory:
            execute_people_target(target, worker_id='fixture-worker')
        finish_task(task.pk, 'fixture-worker')
        task.refresh_from_db()
        return task.target_runs.get()

    def apply(self, task, token, client=None, **extra):
        return (client or self.client).post('/integrations/people/apply/', {
            'task_id': task.pk, 'preview_token': token, 'confirm': 'yes', **extra,
        })


class PeopleImportUITests(PeopleFlowMixin, TestCase):
    def test_provider_tabs_are_import_only_and_credentials_are_write_only(self):
        response = self.client.get('/assets/people/')
        self.assertContains(response, 'id="people-tab-feishu"')
        self.assertContains(response, 'id="people-tab-dingtalk"')
        self.assertContains(response, 'CSV 文件')
        self.assertContains(response, 'API 导入')
        self.assertNotContains(response, 'private-app')
        self.assertContains(response, 'type="password"')
        self.assertNotContains(self.client.get('/assets/networks/'), 'people-tab-feishu')

    def test_invalid_source_save_reopens_tab_without_echoing_or_storing_secrets(self):
        response = self.client.post('/integrations/people/sources/save/', {
            'source_type': 'feishu', 'name': '保留名称', 'source_key': 'invalid key',
            'app_id': 'input-private-id', 'app_secret': 'input-private-secret',
            'root_department_ids': 'root', 'is_enabled': 'on',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '保留名称')
        self.assertContains(response, 'data-auto-open="true"')
        self.assertNotContains(response, 'input-private')
        self.assertNotIn('input-private', str(dict(self.client.session)))
        self.assertEqual(PeopleSyncSource.objects.count(), 2)

    def test_blank_credentials_preserve_selected_source_and_source_identity(self):
        response = self.client.post('/integrations/people/sources/save/', {
            'source_id': self.other.pk, 'source_type': 'feishu', 'source_key': 'branch',
            'name': '更新分部', 'root_department_ids': 'root, child', 'is_enabled': 'on',
            'app_id': '', 'app_secret': '',
        })
        self.assertEqual(response.status_code, 302)
        self.other.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(self.other.credentials['app_secret'], 'branch-secret')
        self.assertEqual(self.other.root_department_ids, ['root', 'child'])
        self.assertEqual(self.source.name, '总部目录')
        response = self.client.post('/integrations/people/sources/save/', {
            'source_id': self.other.pk, 'source_type': 'dingtalk', 'source_key': 'hq',
            'name': 'hijack', 'app_key': 'key', 'app_secret': 'secret',
        })
        self.assertEqual(response.status_code, 200)
        self.other.refresh_from_db()
        self.assertEqual((self.other.source_type, self.other.source_key), ('feishu', 'branch'))

    def test_durable_preview_then_explicit_database_only_apply_exact_saved_diff(self):
        old = People.objects.create(employee_id='OLD', name='旧员工', source='feishu',
                                    sync_source=self.source, is_active=True)
        protected = People.objects.create(employee_id='OTHER', source='feishu',
                                          sync_source=self.other, is_active=True)
        task, url = self.enqueue()
        self.assertEqual(task.status, 'queued')
        self.assertContains(self.client.get(url), '等待')
        self.assertEqual(People.objects.count(), 2)
        target = self.execute(task)
        self.assertEqual(task.status, 'success')
        response = self.client.get(url)
        self.assertContains(response, '预览员工')
        self.assertContains(response, '仅停用当前来源')
        self.assertContains(response, 'OLD')
        self.assertContains(response, '确认应用')
        token = target.result_snapshot['preview']['token']
        self.assertEqual(self.apply(task, token, confirm='').status_code, 400)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Web outbound I/O')):
            response = self.apply(task, token)
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(response['Location']), '新增 1')
        old.refresh_from_db()
        protected.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertTrue(protected.is_active)
        person = People.objects.get(employee_id='P4-001')
        self.assertEqual(person.sync_source_id, self.source.pk)
        self.assertEqual(person.name, '预览员工')
        self.assertEqual(self.apply(task, token).status_code, 400)

    def test_another_session_cannot_view_export_or_apply_even_with_valid_token(self):
        task, url = self.enqueue()
        target = self.execute(task)
        stranger = Client()
        self.assertEqual(stranger.get(url).status_code, 404)
        self.assertEqual(stranger.get(reverse('task_detail', args=[task.pk])).status_code, 404)
        self.assertEqual(stranger.get(reverse('table_export_scoped', args=['task_targets', task.pk])).status_code, 404)
        self.assertEqual(self.apply(task, target.result_snapshot['preview']['token'], client=stranger).status_code, 404)
        self.assertFalse(People.objects.exists())

    def test_source_changed_token_tampered_and_expired_preview_are_rejected(self):
        task, _ = self.enqueue()
        target = self.execute(task)
        token = target.result_snapshot['preview']['token']
        self.assertEqual(self.apply(task, token + 'x').status_code, 400)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 301):
            self.assertEqual(self.apply(task, token).status_code, 400)
        self.source.credentials['app_secret'] = 'rotated-secret'
        self.source.save()
        self.assertEqual(self.apply(task, token).status_code, 400)
        self.assertFalse(People.objects.exists())

    def test_disabled_source_and_duplicate_operation_are_not_queued(self):
        task, _ = self.enqueue()
        response = self.client.post('/integrations/people/preview/', {'source_id': self.source.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '活动')
        self.assertEqual(TaskRun.objects.count(), 1)

    def test_queue_and_expired_apply_errors_are_visible_inside_reopened_modal(self):
        from index.test_phase1_exports import extract_div
        task, _ = self.enqueue()
        response = self.client.post('/integrations/people/preview/', {'source_id': self.source.pk})
        self.assertIn('活动操作', extract_div(response.content.decode(), 'importModal'))
        target = self.execute(task)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 301):
            response = self.apply(task, target.result_snapshot['preview']['token'])
        self.assertEqual(response.status_code, 400)
        self.assertIn('预览已失效', extract_div(response.content.decode(), 'peoplePreviewModal'))
        self.other.is_enabled = False
        self.other.save()
        response = self.client.post('/integrations/people/preview/', {'source_id': self.other.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TaskRun.objects.count(), 1)

    def test_post_and_csrf_required_for_every_write_including_legacy_api_entry(self):
        paths = ['/integrations/people/sources/save/', '/integrations/people/test/',
                 '/integrations/people/preview/', '/integrations/people/apply/',
                 '/data/people/api/feishu/']
        csrf_client = Client(enforce_csrf_checks=True)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)
                self.assertEqual(csrf_client.post(path, {}).status_code, 403)


class PeopleQueueTests(PeopleFlowMixin, TestCase):
    def test_test_connection_is_durable_nonsecret_and_has_no_personnel_write(self):
        task, url = self.enqueue('test', source=self.other)
        target = self.execute(task)
        self.assertEqual(task.people_source_id, self.other.pk)
        self.assertEqual(task.status, 'success')
        self.assertContains(self.client.get(url), '连接测试成功')
        self.assertNotContains(self.client.get(url), '确认应用')
        self.other.refresh_from_db()
        self.assertIsNotNone(self.other.last_tested_at)
        snapshots = str([task.profile_snapshot, task.parameters_snapshot, target.target_snapshot, target.result_snapshot])
        self.assertNotIn('branch-secret', snapshots)
        self.assertNotIn(self.client.session.session_key, snapshots)
        self.assertFalse(People.objects.exists())

    def test_changed_source_before_worker_and_incomplete_snapshot_fail_closed(self):
        task, _ = self.enqueue()
        self.source.credentials['app_secret'] = 'rotated'
        self.source.save()
        target = self.execute(task)
        self.assertEqual(task.status, 'failed')
        self.assertNotIn('preview', target.result_snapshot)
        task, _ = self.enqueue()
        self.factory = patch('net.tasks.executors.people.build_directory_adapter',
                             lambda source: SnapshotAdapter(source, [], complete=False))
        target = self.execute(task)
        self.assertEqual(task.status, 'failed')
        self.assertEqual(self.apply(task, 'not-a-preview').status_code, 400)
        self.assertFalse(People.objects.exists())

    def test_lost_lease_cannot_publish_preview_or_test_timestamp(self):
        task, _ = self.enqueue('test')
        def lose_lease(source):
            adapter = FixtureDirectory(source)
            def test_connection():
                TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
            adapter.test_connection = test_connection
            return adapter
        from net.tasks.executors.people import execute_people_target
        claimed = claim_next_task('old-worker', 60)
        with patch('net.tasks.executors.people.build_directory_adapter', lose_lease):
            result = execute_people_target(claimed.target_runs.get(), worker_id='old-worker')
        self.assertTrue(result.stale)
        target = task.target_runs.get()
        self.assertEqual(target.result_snapshot, {})
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_tested_at)
        recover_expired_tasks()
        task.refresh_from_db()
        self.assertEqual(task.status, 'queued')

    def test_source_binding_target_shape_and_snapshots_are_immutable(self):
        task, _ = self.enqueue()
        original_scope = task.scope_key
        task.people_source = self.other
        with self.assertRaises(ValidationError):
            task.save()
        task.refresh_from_db()
        task.parameters_snapshot['owner_session_digest'] = '0' * 64
        with self.assertRaises(ValidationError):
            task.save()
        task.refresh_from_db()
        target = task.target_runs.get()
        target.target_id = str(self.other.pk)
        with self.assertRaises(ValidationError):
            target.full_clean()
        task.inspection_profile_id = self.other.pk
        with self.assertRaises(ValidationError):
            task.clean()
        task.refresh_from_db()
        self.assertEqual(task.scope_key, original_scope)

    def test_old_invocation_cannot_begin_after_same_worker_id_reclaims(self):
        from net.tasks.executors.people import execute_people_target
        task, _ = self.enqueue()
        first_claim = claim_next_task('same-worker', 60)
        old_target = task.target_runs.get()
        old_target.task = first_claim
        TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        recover_expired_tasks()
        claim_next_task('same-worker', 60)
        with self.factory:
            outcome = execute_people_target(old_target, worker_id='same-worker')
        self.assertTrue(outcome.stale)
        self.assertEqual(task.target_runs.get().status, 'queued')

    def test_unexpected_failure_path_is_fenced_to_original_claim(self):
        from net.tasks.executors.people import persist_people_failure
        task, _ = self.enqueue()
        first_claim = claim_next_task('same-worker', 60)
        old_target = task.target_runs.get()
        old_target.task = first_claim
        TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        recover_expired_tasks()
        claim_next_task('same-worker', 60)
        from net.tasks.executors.inspection import _begin_target
        _begin_target(old_target.pk, 'same-worker')
        outcome = persist_people_failure(old_target, worker_id='same-worker', error='private-secret')
        self.assertTrue(outcome.stale)
        self.assertEqual(task.target_runs.get().status, 'running')

    def test_source_changed_during_fetch_and_mismatched_frozen_adapter_fail_closed(self):
        task, _ = self.enqueue()
        def change_source():
            PeopleSyncSource.objects.filter(pk=self.source.pk).update(root_department_ids=['changed'])
        self.factory = patch('net.tasks.executors.people.build_directory_adapter',
                             lambda source: SnapshotAdapter(source, [], before_iter=change_source))
        target = self.execute(task)
        self.assertEqual(task.status, 'failed')
        self.assertNotIn('preview', target.result_snapshot)
        task, _ = self.enqueue()
        self.factory = patch('net.tasks.executors.people.build_directory_adapter',
                             lambda source: FixtureDirectory(self.other))
        target = self.execute(task)
        self.assertEqual(task.status, 'failed')
        self.assertNotIn('preview', target.result_snapshot)

    def test_duplicate_other_session_is_rejected_but_different_source_is_independent(self):
        self.enqueue()
        other_client = Client()
        response = other_client.post('/integrations/people/preview/', {'source_id': self.source.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TaskRun.objects.count(), 1)
        other_task, _ = self.enqueue(source=self.other, client=other_client)
        self.assertEqual(TaskRun.objects.count(), 2)
        self.assertEqual(other_task.people_source_id, self.other.pk)

    def test_disabled_before_apply_or_local_changes_cannot_apply(self):
        task, _ = self.enqueue()
        target = self.execute(task)
        token = target.result_snapshot['preview']['token']
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(is_enabled=False)
        self.assertEqual(self.apply(task, token).status_code, 400)
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(is_enabled=True)
        People.objects.create(employee_id='LOCAL', name='本地录入')
        self.assertEqual(self.apply(task, token).status_code, 400)
        self.assertEqual(list(People.objects.values_list('employee_id', flat=True)), ['LOCAL'])

    def test_saved_preview_payload_tampering_and_nonterminal_task_cannot_apply(self):
        task, _ = self.enqueue()
        self.assertEqual(self.apply(task, 'arbitrary').status_code, 400)
        target = self.execute(task)
        token = target.result_snapshot['preview']['token']
        target.result_snapshot['preview']['creates'][0]['name'] = 'tampered'
        # Simulate corruption outside normal immutable model saves.
        TaskTargetRun.objects.filter(pk=target.pk).update(result_snapshot=target.result_snapshot)
        self.assertEqual(self.apply(task, token).status_code, 400)
        self.assertFalse(People.objects.exists())


class PeopleWorkerIntegrationTests(PeopleFlowMixin, TransactionTestCase):
    def test_real_worker_dispatches_success_and_failure_without_alert_reconciliation(self):
        from net.tasks.worker import TaskWorker
        from net.alerts.service import process_persisted_target, reconcile_terminal_targets
        for failure in (False, True):
            task, _ = self.enqueue()
            factory = (patch('net.tasks.executors.people.build_directory_adapter',
                             side_effect=RuntimeError('private-app-secret')) if failure else self.factory)
            with factory:
                self.assertTrue(TaskWorker(worker_id='real-worker', threads=2).run_once())
            task.refresh_from_db()
            self.assertEqual(task.status, 'failed' if failure else 'success')
            target = task.target_runs.get()
            process_persisted_target(target)
            reconcile_terminal_targets()
            target.refresh_from_db()
            self.assertIsNone(target.alert_attempted_at)
            self.assertIsNone(target.alert_processed_at)
            self.assertEqual(target.alert_processing_error, '')
            self.assertNotIn('private-app-secret', str(target.result_snapshot) + task.error_summary)
        self.assertFalse(People.objects.exists())
