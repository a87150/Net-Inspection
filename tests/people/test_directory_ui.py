"""Personnel import acceptance: real queue/preview/apply, fixture-only I/O."""

from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from tests.people.test_directory_sync import SnapshotAdapter
from tests.auth import login_admin
from net.people.directory import DirectoryPerson
from net.models import People, PeopleSyncSource, Schedule, TaskRun, TaskTargetRun
from net.inspections.queue import claim_next_task, finish_task, recover_expired_tasks


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
        login_admin(self.client)
        self.source = PeopleSyncSource.objects.create(
            name='飞书', source_key='people-provider-feishu', source_type='feishu',
            credentials={'app_id': 'private-app-id', 'app_secret': 'private-app-secret'},
        )
        self.other = PeopleSyncSource.objects.create(
            name='钉钉', source_key='people-provider-dingtalk', source_type='dingtalk',
            credentials={'app_key': 'branch-app', 'app_secret': 'branch-secret'},
        )
        PeopleSyncSource.objects.filter(pk__in=[self.source.pk, self.other.pk]).update(
            last_tested_at=timezone.now(),
        )
        self.source.refresh_from_db()
        self.other.refresh_from_db()
        self.factory = patch('net.people.executor.build_directory_adapter', FixtureDirectory)
        # Start only once the public entry exists: RED is missing UI behavior, not import errors.

    def enqueue(self, operation='preview', source=None, client=None):
        source = source or self.source
        response = (client or self.client).post(
            f'/integrations/people/{operation}/', {
                'provider': source.source_type,
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        task = TaskRun.objects.latest('created_at')
        self.assertEqual(
            response['Location'],
            f'/assets/people/?import=people&provider={source.source_type}',
        )
        return task, reverse('people_operation', args=[task.pk])

    def execute(self, task):
        from net.people.executor import execute_people_target
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
    def test_rejected_preview_returns_to_get_page_and_keeps_modal_open(self):
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=None)
        response = self.client.post('/integrations/people/preview/?page_size=500', {'provider': 'feishu'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/assets/people/?import=people&provider=feishu')
        page = self.client.get(response['Location'])
        self.assertContains(page, '请先完成第 1 步')
        self.assertTrue(page.context['open_import_modal'])
        self.assertFalse(TaskRun.objects.exists())

    def test_fixed_provider_can_save_interval_and_daily_schedule(self):
        response = self.client.post('/integrations/people/providers/feishu/schedule/', {
            'is_enabled': 'on', 'kind': 'interval',
            'interval_value': '2', 'interval_unit': 'hours', 'daily_time': '',
        })
        self.assertEqual(response.status_code, 302)
        schedule = Schedule.objects.get(people_source=self.source)
        self.assertEqual(
            (schedule.kind, schedule.interval_value, schedule.interval_unit),
            ('interval', 2, 'hours'),
        )
        self.assertIsNotNone(schedule.next_run_at)

        response = self.client.post('/integrations/people/providers/feishu/schedule/', {
            'is_enabled': 'on', 'kind': 'daily',
            'interval_value': '', 'interval_unit': '', 'daily_time': '08:30',
        })
        self.assertEqual(response.status_code, 302)
        schedule.refresh_from_db()
        self.assertEqual(schedule.kind, 'daily')
        self.assertEqual(schedule.daily_time.strftime('%H:%M'), '08:30')
        self.assertIsNone(schedule.interval_value)

    def test_schedule_requires_current_connection_test_and_survives_config_change(self):
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=None)
        response = self.client.post('/integrations/people/providers/feishu/schedule/', {
            'is_enabled': 'on', 'kind': 'interval',
            'interval_value': '30', 'interval_unit': 'minutes', 'daily_time': '',
        }, follow=True)
        self.assertContains(response, '必须先通过当前配置的连接测试')
        self.assertFalse(Schedule.objects.exists())

        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=timezone.now())
        self.client.post('/integrations/people/providers/feishu/schedule/', {
            'is_enabled': 'on', 'kind': 'interval',
            'interval_value': '30', 'interval_unit': 'minutes', 'daily_time': '',
        })
        self.client.post('/integrations/people/providers/feishu/save/', {
            'root_department_ids': 'changed', 'is_enabled': 'on',
            'app_id': '', 'app_secret': '',
        })
        schedule = Schedule.objects.get(people_source=self.source)
        self.assertTrue(schedule.is_enabled)
        response = self.client.get('/assets/people/?import=people&provider=feishu')
        self.assertContains(response, '等待重新测试')
    def test_preview_requires_a_successful_connection_test_for_current_source(self):
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=None)

        response = self.client.post(
            '/integrations/people/preview/', {'provider': 'feishu'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '请先完成第 1 步“测试连接”')
        self.assertEqual(TaskRun.objects.count(), 0)

    def test_people_import_shows_only_fixed_provider_settings_and_three_stages(self):
        response = self.client.get(
            '/assets/people/?import=people&provider=feishu',
        )

        self.assertContains(response, '飞书 API 设置')
        self.assertContains(response, '钉钉 API 设置')
        self.assertContains(response, '第 1 步：测试连接')
        self.assertContains(response, '第 2 步：预览数据')
        self.assertContains(response, '第 3 步：执行导入')
        self.assertNotContains(response, '新建来源')
        self.assertNotContains(response, '来源名称')
        self.assertNotContains(response, '稳定来源标识')
        self.assertNotContains(response, 'name="source_id"')
        self.assertContains(response, 'data-people-task-notifications')
        self.assertContains(response, reverse('people_task_status'))

    def test_provider_tabs_are_import_only_and_credentials_are_write_only(self):
        response = self.client.get('/assets/people/')
        document = response.content.decode(response.charset)

        self.assertIn('>导入人员</button>', document)
        self.assertNotIn('>API 导入</a>', document)
        self.assertContains(response, '>文件</button>')
        self.assertContains(response, '可点击右上角“导入人员”添加人员。')
        self.assertContains(response, 'id="people-tab-feishu"')
        self.assertContains(response, 'id="people-tab-dingtalk"')
        self.assertContains(response, 'CSV 或 Excel 文件')
        self.assertContains(response, '飞书 API 设置')
        self.assertContains(response, '钉钉 API 设置')
        self.assertNotContains(response, 'private-app')
        self.assertContains(response, 'type="password"')
        self.assertNotContains(self.client.get('/assets/networks/'), 'people-tab-feishu')

    def test_invalid_provider_save_redirects_to_fixed_tab_without_secrets(self):
        response = self.client.post('/integrations/people/providers/feishu/save/?page_size=500', {
            'app_id': 'input-private-id', 'app_secret': 'input-private-secret',
            'root_department_ids': 'root, root', 'is_enabled': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/assets/people/?'))
        self.assertIn('import=people', response.url)
        self.assertIn('provider=feishu', response.url)

        response = self.client.get(response.url)

        self.assertContains(response, '根部门不能重复')
        self.assertContains(response, 'data-auto-open="true"')
        self.assertNotContains(response, 'input-private')
        self.assertNotIn('input-private', str(dict(self.client.session)))
        self.assertEqual(PeopleSyncSource.objects.count(), 2)

    def test_blank_credentials_preserve_canonical_source_identity(self):
        response = self.client.post('/integrations/people/providers/dingtalk/save/', {
            'root_department_ids': 'root, child', 'is_enabled': 'on',
            'app_key': '', 'app_secret': '',
        })
        self.assertEqual(response.status_code, 302)
        self.other.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(self.other.credentials['app_secret'], 'branch-secret')
        self.assertEqual(self.other.root_department_ids, ['root', 'child'])
        self.assertEqual((self.other.name, self.other.source_type, self.other.source_key), (
            '钉钉', 'dingtalk', 'people-provider-dingtalk',
        ))
        self.assertEqual(self.source.name, '飞书')

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
        self.assertContains(response, '仅停用当前 API 来源')
        self.assertContains(response, 'OLD')
        self.assertContains(response, '确认应用')
        self.assertContains(response, '导入数据')
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

    def test_queued_people_operation_does_not_refresh_the_page(self):
        _task, url = self.enqueue()

        response = self.client.get(url)

        self.assertNotIn('Refresh', response.headers)

    def test_status_returns_only_tasks_created_by_current_session(self):
        own, _ = self.enqueue('test')
        other_client = Client()
        login_admin(other_client)
        other, _ = self.enqueue('preview', source=self.other, client=other_client)

        response = self.client.get('/integrations/people/tasks/status/')

        self.assertEqual(response.status_code, 200)
        task_ids = {item['id'] for item in response.json()['tasks']}
        self.assertIn(str(own.pk), task_ids)
        self.assertNotIn(str(other.pk), task_ids)
        self.assertNotIn('parameters_snapshot', response.content.decode())
        self.assertNotIn('private-app', response.content.decode())

    def test_running_and_terminal_acknowledgements_are_independent(self):
        task, _ = self.enqueue('test')
        ack_url = reverse('people_task_acknowledge', args=[task.pk])

        response = self.client.post(ack_url, {'kind': 'running'})
        self.assertEqual(response.status_code, 200)
        running = self.client.get('/integrations/people/tasks/status/').json()['tasks'][0]
        self.assertFalse(running['show_running'])
        self.assertFalse(running['show_terminal'])

        self.execute(task)
        terminal = self.client.get('/integrations/people/tasks/status/').json()['tasks'][0]
        self.assertFalse(terminal['show_running'])
        self.assertTrue(terminal['show_terminal'])
        self.assertEqual(terminal['jump_url'], reverse('people_operation', args=[task.pk]))

        self.client.post(ack_url, {'kind': 'terminal'})
        self.assertEqual(
            self.client.get('/integrations/people/tasks/status/').json()['tasks'],
            [],
        )

    def test_task_acknowledgement_rejects_invalid_kind_and_other_session(self):
        task, _ = self.enqueue('test')
        ack_url = reverse('people_task_acknowledge', args=[task.pk])

        self.assertEqual(self.client.post(ack_url, {'kind': 'unknown'}).status_code, 400)
        other_client = Client()
        login_admin(other_client)
        self.assertEqual(other_client.post(ack_url, {'kind': 'running'}).status_code, 404)

    def test_another_session_cannot_view_export_or_apply_even_with_valid_token(self):
        task, url = self.enqueue()
        target = self.execute(task)
        stranger = Client()
        login_admin(stranger)
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
        response = self.client.post('/integrations/people/preview/', {'provider': 'feishu'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '活动')
        self.assertEqual(TaskRun.objects.count(), 1)

    def test_queue_and_expired_apply_errors_are_visible_inside_reopened_modal(self):
        from tests.common.test_table_exports import extract_div
        task, _ = self.enqueue()
        response = self.client.post('/integrations/people/preview/', {'provider': 'feishu'}, follow=True)
        self.assertIn('活动操作', extract_div(response.content.decode(), 'importModal'))
        target = self.execute(task)
        with patch('django.core.signing.time.time', return_value=timezone.now().timestamp() + 301):
            response = self.apply(task, target.result_snapshot['preview']['token'])
        self.assertEqual(response.status_code, 400)
        self.assertIn('预览已失效', extract_div(response.content.decode(), 'peoplePreviewModal'))
        self.other.is_enabled = False
        self.other.save()
        response = self.client.post('/integrations/people/preview/', {'provider': 'dingtalk'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TaskRun.objects.count(), 1)

    def test_post_and_csrf_required_for_every_current_write(self):
        paths = ['/integrations/people/providers/feishu/save/', '/integrations/people/test/',
                 '/integrations/people/preview/', '/integrations/people/apply/']
        csrf_client = Client(enforce_csrf_checks=True)
        login_admin(csrf_client)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)
                self.assertEqual(csrf_client.post(path, {}).status_code, 403)

        page = csrf_client.get('/assets/people/?import=people&provider=feishu')
        token = page.cookies['csrftoken'].value
        response = csrf_client.post(
            '/integrations/people/preview/', {'provider': 'feishu'},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 302)
        task = TaskRun.objects.latest('created_at')
        target = self.execute(task)
        preview = csrf_client.get(reverse('people_operation', args=[task.pk]))
        self.assertContains(preview, '预览员工')
        payload = {
            'task_id': task.pk,
            'preview_token': target.result_snapshot['preview']['token'],
            'confirm': 'yes',
        }
        self.assertEqual(csrf_client.post('/integrations/people/apply/', payload).status_code, 403)
        self.assertFalse(People.objects.exists())
        applied = csrf_client.post(
            '/integrations/people/apply/', payload, HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(applied.status_code, 302)
        self.assertTrue(People.objects.filter(employee_id='P4-001', name='预览员工').exists())

    def test_legacy_multi_source_routes_are_not_routable(self):
        self.assertEqual(self.client.post('/integrations/people/sources/save/').status_code, 404)
        self.assertEqual(self.client.post('/data/people/api/feishu/').status_code, 404)


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

    def test_successful_connection_offers_preview_as_the_next_step(self):
        task, url = self.enqueue('test')
        self.execute(task)

        response = self.client.get(url)

        self.assertContains(response, '第 2 步：预览数据')
        self.assertContains(response, reverse('people_preview'))
        self.assertContains(response, 'name="provider" value="feishu"')

    def test_changed_source_before_worker_and_incomplete_snapshot_fail_closed(self):
        task, _ = self.enqueue()
        self.source.credentials['app_secret'] = 'rotated'
        self.source.save()
        target = self.execute(task)
        self.assertEqual(task.status, 'failed')
        self.assertNotIn('preview', target.result_snapshot)
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=timezone.now())
        self.source.refresh_from_db()
        task, _ = self.enqueue()
        self.factory = patch('net.people.executor.build_directory_adapter',
                             lambda source: SnapshotAdapter(source, [], complete=False))
        target = self.execute(task)
        self.assertEqual(task.status, 'failed')
        self.assertEqual(self.apply(task, 'not-a-preview').status_code, 400)
        self.assertFalse(People.objects.exists())

    def test_lost_lease_cannot_publish_preview_or_test_timestamp(self):
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=None)
        self.source.refresh_from_db()
        task, _ = self.enqueue('test')
        def lose_lease(source):
            adapter = FixtureDirectory(source)
            def test_connection():
                TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
            adapter.test_connection = test_connection
            return adapter
        from net.people.executor import execute_people_target
        claimed = claim_next_task('old-worker', 60)
        with patch('net.people.executor.build_directory_adapter', lose_lease):
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
        from net.people.executor import execute_people_target
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
        from net.people.executor import persist_people_failure
        task, _ = self.enqueue()
        first_claim = claim_next_task('same-worker', 60)
        old_target = task.target_runs.get()
        old_target.task = first_claim
        TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        recover_expired_tasks()
        claim_next_task('same-worker', 60)
        from net.inspections.executor import _begin_target
        _begin_target(old_target.pk, 'same-worker')
        outcome = persist_people_failure(old_target, worker_id='same-worker', error='private-secret')
        self.assertTrue(outcome.stale)
        self.assertEqual(task.target_runs.get().status, 'running')

    def test_source_changed_during_fetch_and_mismatched_frozen_adapter_fail_closed(self):
        task, _ = self.enqueue()
        def change_source():
            PeopleSyncSource.objects.filter(pk=self.source.pk).update(root_department_ids=['changed'])
        self.factory = patch('net.people.executor.build_directory_adapter',
                             lambda source: SnapshotAdapter(source, [], before_iter=change_source))
        target = self.execute(task)
        self.assertEqual(task.status, 'failed')
        self.assertNotIn('preview', target.result_snapshot)
        task, _ = self.enqueue()
        self.factory = patch('net.people.executor.build_directory_adapter',
                             lambda source: FixtureDirectory(self.other))
        target = self.execute(task)
        self.assertEqual(task.status, 'failed')
        self.assertNotIn('preview', target.result_snapshot)

    def test_duplicate_other_session_is_rejected_but_different_source_is_independent(self):
        self.enqueue()
        other_client = Client()
        login_admin(other_client)
        response = other_client.post('/integrations/people/preview/', {'provider': 'feishu'}, follow=True)
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
        from net.inspections.worker import TaskWorker
        from net.alerts.service import process_persisted_target, reconcile_terminal_targets
        for failure in (False, True):
            task, _ = self.enqueue()
            factory = (patch('net.people.executor.build_directory_adapter',
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
