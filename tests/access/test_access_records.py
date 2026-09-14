from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from net.access.adapters import AccessEvent, V6600_V6000_COMPAT_VERSION, build_adapter
from net.access.tasks import enqueue_access_sync
from net.inspections.worker import TaskWorker
from net.models.access import AccessRecord, AccessRecordSource
from net.models.tasks import TaskRun
from tests.auth import login_admin, login_reader

KEY = 'V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E='


class FixtureAdapter:
    def __init__(self, source, events=(), error=None):
        self.events, self.error = events, error

    def fetch_events(self, *, start_at, end_at, cancelled):
        if self.error:
            raise self.error
        return iter(self.events)


@override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=KEY)
class AccessSourceAndAdapterTests(TestCase):
    def source(self, **changes):
        values = {'name': 'V6600', 'platform': 'zkteco_v6600', 'api_version': V6600_V6000_COMPAT_VERSION,
                  'base_url': 'https://v6600.example.test:8088', 'event_path': '/documented/access/events'}
        values.update(changes)
        source = AccessRecordSource(**values)
        source.set_credentials({'access_token': 'never-render-this'})
        source.full_clean(); source.save()
        return source

    def test_credentials_encrypt_and_public_data_never_exposes_secret(self):
        source = self.source()
        source.refresh_from_db()
        self.assertEqual(source.get_credentials()['access_token'], 'never-render-this')
        self.assertNotIn('never-render-this', str(source.public_data()))
        self.assertNotIn('credential', source.public_data())

    def test_relative_event_path_is_required_and_rejects_other_origin(self):
        with self.assertRaises(ValidationError):
            self.source(event_path='https://evil.example/events')

    def test_adapter_rejects_unsafe_response_before_empty_success(self):
        source = self.source()
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'code': 1, 'data': [{'id': '', 'eventTime': '2026-09-09 10:00:00'}]}
        with patch('net.access.adapters.requests.get', return_value=Response()):
            with self.assertRaises(ValidationError):
                list(build_adapter(source).fetch_events(start_at=timezone.now() - timedelta(minutes=1), end_at=timezone.now(), cancelled=None))

    def test_adapter_paginates_without_sending_token_in_url(self):
        source = self.source()
        calls = []
        class Response:
            def __init__(self, data): self.data = data
            def raise_for_status(self): pass
            def json(self): return {'code': 1, 'data': self.data}
        pages = [
            [{'id': str(i), 'eventTime': '2026-09-09 10:00:00'} for i in range(1000)],
            [{'id': 'last', 'eventTime': '2026-09-09 10:00:01'}],
        ]
        def request(url, **kwargs):
            calls.append((url, kwargs)); return Response(pages.pop(0))
        with patch('net.access.adapters.requests.get', side_effect=request):
            events = list(build_adapter(source).fetch_events(start_at=timezone.now() - timedelta(minutes=1), end_at=timezone.now(), cancelled=None))
        self.assertEqual(len(events), 1001)
        self.assertEqual([item[1]['params']['pageNo'] for item in calls], [1, 2])
        self.assertTrue(all('never-render-this' not in item[0] for item in calls))


@override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=KEY)
class AccessWorkerTests(TransactionTestCase):
    def setUp(self):
        self.source = AccessRecordSource(name='V6600', platform='zkteco_v6600', api_version=V6600_V6000_COMPAT_VERSION, base_url='https://v6600.example.test', event_path='/documented/events')
        self.source.set_credentials({'access_token': 'worker-token'})
        self.source.save()

    def enqueue(self):
        return enqueue_access_sync([self.source.pk], start_at=timezone.now() - timedelta(minutes=1), end_at=timezone.now())[0]

    def test_worker_persists_idempotently_and_advances_cursor_only_on_success(self):
        task = self.enqueue()
        events = [AccessEvent('event-1', timezone.now(), employee_number='E1', card_number='C1'), AccessEvent('event-1', timezone.now(), employee_number='E1', card_number='C1')]
        with patch('net.access.executor.build_adapter', return_value=FixtureAdapter(self.source, events)):
            self.assertTrue(TaskWorker(worker_id='access-worker', threads=1).run_once())
        task.refresh_from_db(); self.source.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.SUCCESS)
        self.assertEqual(AccessRecord.objects.count(), 1)
        self.assertIsNotNone(self.source.cursor_at)
        snapshots = str([task.profile_snapshot, task.parameters_snapshot, task.target_runs.get().target_snapshot])
        self.assertNotIn('worker-token', snapshots)

    def test_failure_and_configuration_change_do_not_write_or_advance_cursor(self):
        task = self.enqueue()
        old_cursor = self.source.cursor_at
        AccessRecordSource.objects.filter(pk=self.source.pk).update(event_path='/changed')
        with patch('net.access.executor.build_adapter', return_value=FixtureAdapter(self.source, [])):
            TaskWorker(worker_id='access-worker', threads=1).run_once()
        task.refresh_from_db(); self.source.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.FAILED)
        self.assertEqual(AccessRecord.objects.count(), 0)
        self.assertEqual(self.source.cursor_at, old_cursor)
        self.assertIn('配置已变更', task.target_runs.get().error_message)

    def test_provider_failure_does_not_advance_cursor(self):
        task = self.enqueue()
        with patch('net.access.executor.build_adapter', return_value=FixtureAdapter(self.source, error=ValidationError('fixture failure'))):
            TaskWorker(worker_id='access-worker', threads=1).run_once()
        task.refresh_from_db(); self.source.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.FAILED)
        self.assertIsNone(self.source.cursor_at)


@override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=KEY)
class AccessPermissionsTests(TestCase):
    def setUp(self):
        self.source = AccessRecordSource(name='V6600', platform='zkteco_v6600', api_version=V6600_V6000_COMPAT_VERSION, base_url='https://v6600.example.test', event_path='/events')
        self.source.set_credentials({'access_token': 'ui-secret'}); self.source.save()
        AccessRecord.objects.create(source=self.source, source_event_id='one', occurred_at=timezone.now(), person_name='Readable')

    def test_reader_can_view_records_but_cannot_configure_or_sync(self):
        login_reader(self.client)
        self.assertContains(self.client.get(reverse('access_record_list')), 'Readable')
        self.assertEqual(self.client.post(reverse('access_record_sync'), {'source_ids': [self.source.pk]}).status_code, 403)

    def test_connection_test_saves_submitted_endpoint_and_token(self):
        login_admin(self.client)
        class Probe:
            def test_connection(self):
                return None
        with patch('index.access.views.build_adapter', return_value=Probe()):
            response = self.client.post(reverse('access_source_test'), {
                'source_id': self.source.pk, 'name': 'Retained', 'platform': 'zkteco_v6600',
                'api_version': V6600_V6000_COMPAT_VERSION, 'base_url': 'https://changed.example.test',
                'event_path': '/documented/new-events', 'authentication_mode': 'header_token',
                'authentication_name': 'X-Access-Token', 'verify_ssl': 'on', 'is_enabled': 'on',
                'default_lookback_minutes': '60', 'credential': 'replacement-secret',
            })
        self.assertEqual(response.status_code, 302)
        self.source.refresh_from_db()
        self.assertEqual((self.source.name, self.source.event_path, self.source.authentication_mode),
                         ('Retained', '/documented/new-events', 'header_token'))
        self.assertEqual(self.source.get_credentials()['access_token'], 'replacement-secret')
    def test_admin_save_never_reveals_token(self):
        login_admin(self.client)
        response = self.client.post(reverse('access_source_save'), {
            'name': 'Updated', 'platform': 'zkteco_v6600', 'api_version': V6600_V6000_COMPAT_VERSION,
            'base_url': 'https://v6600.example.test', 'event_path': '/events',
            'authentication_mode': 'query_token', 'authentication_name': 'access_token',
            'verify_ssl': 'on', 'is_enabled': 'on', 'default_lookback_minutes': '60', 'credential': 'fresh-secret',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'fresh-secret')
        self.assertNotContains(response, 'ui-secret')

    def test_admin_record_page_opens_config_modal_and_reader_cannot_access_it(self):
        login_admin(self.client)
        response=self.client.get(reverse('access_record_list'))
        self.assertContains(response,'data-bs-target="#accessSourceModal"')
        self.assertContains(response,'id="accessSourceForm"')
        self.assertContains(response,'保存并测试连接')
        self.assertNotContains(response,'ui-secret')
        login_reader(self.client)
        response=self.client.get(reverse('access_record_list'))
        self.assertNotContains(response,'id="accessSourceForm"')
        self.assertEqual(self.client.get(reverse('access_source_settings')).status_code,403)

    def test_modal_validation_error_retains_fields_and_saved_response_retains_editor(self):
        login_admin(self.client)
        response=self.client.post(reverse('access_source_save'),{'name':'Keep draft','base_url':'bad'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'id="accessSourceModal"',status_code=400)
        self.assertContains(response,'Keep draft',status_code=400)
        response=self.client.post(reverse('access_source_save'),{
            'name':'Modal source','platform':'zkteco_v6600','api_version':V6600_V6000_COMPAT_VERSION,
            'base_url':'https://v6600.example.test','event_path':'/events','authentication_mode':'query_token',
            'authentication_name':'access_token','is_enabled':'on','default_lookback_minutes':'60'},follow=True,
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertContains(response,'id="accessSourceModal"')
        self.assertContains(response,'data-source-saved="true"')
