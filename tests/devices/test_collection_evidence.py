import io
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.test import TestCase
from django.urls import reverse
from tests.auth import login_reader
from django.utils import timezone

from net.models import (AlertChannel, AlertDelivery, AlertEvent, InspectionProfile,
                        People, Server, Server_Inspection)
from net.infrastructure.http_collectors import collect_windows_http
from net.infrastructure.ssh_collectors import collect_linux_ssh
from net.data_exchange.inventory_csv import import_csv
from net.inspections.queue import enqueue_task, claim_next_task
from net.inspections.executor import execute_target


class FinalEvidenceTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        self.server = Server.objects.create(name='HTTP evidence', ip='192.0.2.22', server_type='windows',
            api_url='https://u:p@example.invalid/status?opaque=hidden-query#hidden-fragment')
        self.profile = InspectionProfile.objects.create(name='evidence', device_type='server', selected_items=['cpu'])

    def response(self, payload):
        return SimpleNamespace(headers={'content-type': 'application/json'}, json=lambda: payload,
                               raise_for_status=lambda: None)

    def test_http_missing_and_wrong_shape_fail_but_valid_empty_list_succeeds(self):
        for payload in ({}, {'cpu': {}}, {'cpu': 'unknown'}, {'cpu': None}, {'cpu': {'usage_percent': 'unknown'}}):
            with self.subTest(payload=payload), patch('requests.get', return_value=self.response(payload)):
                self.assertEqual(collect_windows_http(self.server, selected_items=['cpu']).status, 'failed')
        with patch('requests.get', return_value=self.response({'services': []})):
            self.assertEqual(collect_windows_http(self.server, selected_items=['services']).status, 'success')

    def test_http_partial_evidence_is_not_normal_recovery(self):
        self.profile.selected_items = ['cpu', 'memory']
        self.profile.save()
        task = enqueue_task(self.profile, [self.server.pk], 'manual')
        claim_next_task('evidence', 60)
        with patch('requests.get', return_value=self.response({'cpu': {'usage_percent': 0}})):
            execute_target(task.target_runs.get(), worker_id='evidence')
        self.assertEqual(Server_Inspection.objects.get().status, 'partial')
        self.assertEqual(task.alert_events.get().event_type, 'abnormal')

    def test_collection_failure_recovers_only_after_complete_evidence(self):
        from net.inspections.queue import finish_task

        self.profile.selected_items = ['cpu', 'services']
        self.profile.save()
        observations = [
            ({}, 'failed', 'abnormal'),
            ({'cpu': {'usage_percent': 0}}, 'partial', 'abnormal'),
            ({'cpu': {'usage_percent': 0}, 'services': []}, 'success', 'recovery'),
        ]
        for payload, status, event_type in observations:
            with self.subTest(status=status):
                task = enqueue_task(self.profile, [self.server.pk], 'manual')
                claim_next_task('evidence', 60)
                with patch('requests.get', return_value=self.response(payload)):
                    execute_target(task.target_runs.get(), worker_id='evidence')
                finish_task(task.pk, 'evidence')
                self.assertEqual(Server_Inspection.objects.get(task_target__task=task).status, status)
                self.assertEqual(list(task.alert_events.values_list('event_type', flat=True)), [event_type])
                event = task.alert_events.get()
                self.assertEqual([finding['key'] for finding in event.findings], ['inspection.collection'])
                self.assertEqual(event.states.get().status, 'normal' if status == 'success' else 'abnormal')

    def test_http_exception_diagnostics_drop_all_query_and_fragment_values(self):
        task = enqueue_task(self.profile, [self.server.pk], 'manual')
        claim_next_task('evidence', 60)
        with patch('requests.get', side_effect=requests.HTTPError(self.server.api_url)):
            execute_target(task.target_runs.get(), worker_id='evidence')
        record = Server_Inspection.objects.get()
        pages = [self.client.get(reverse('task_detail', args=[task.pk])),
                 self.client.get(reverse('record_detail', args=['servers', record.pk])),
                 self.client.get(reverse('table_export', args=['inspection_records']))]
        values = [json.dumps(task.target_runs.get().result_snapshot), record.summary,
                  json.dumps(list(task.alert_events.values('findings')))]
        for page in pages:
            self.assertEqual(page.status_code, 200)
            values.append(b''.join(page.streaming_content).decode() if page.streaming else page.content.decode())
        for value in values:
            self.assertNotIn('hidden-query', value)
            self.assertNotIn('hidden-fragment', value)
        self.server.refresh_from_db()
        self.assertIn('hidden-query', self.server.api_url)

    def test_linux_nonzero_exit_and_missing_output_fail(self):
        for output, error, code in [(b'', b'command not found', 127), (b'', b'', 0), (b'plausible', b'', 1)]:
            client = Mock()
            stdout = Mock()
            stdout.read.return_value = output
            stdout.channel.recv_exit_status.return_value = code
            stderr = Mock()
            stderr.read.return_value = error
            client.exec_command.return_value = (None, stdout, stderr)
            with self.subTest(code=code, output=output), patch('net.infrastructure.ssh_collectors._connect', return_value=client):
                self.assertEqual(collect_linux_ssh(self.server, selected_items=['cpu']).status, 'failed')

    def test_csv_provenance_and_provider_collision_are_atomic(self):
        import_csv('people', io.BytesIO('工号,姓名\nCSV-1,First\n'.encode()))
        self.assertEqual(People.objects.get(employee_id='CSV-1').source, 'csv')
        People.objects.create(employee_id='provider', name='Protected', source='feishu')
        with self.assertRaises(ValueError):
            import_csv('people', io.BytesIO('工号,姓名\nCSV-2,New\nprovider,Changed\n'.encode()))
        self.assertFalse(People.objects.filter(employee_id='CSV-2').exists())
        self.assertEqual(People.objects.get(employee_id='provider').name, 'Protected')
        from net.models import PeopleSyncSource
        from net.people.directory.sync import preview_people_sync, apply_people_sync
        from tests.people.test_directory_sync import SnapshotAdapter
        source = PeopleSyncSource.objects.create(name='CSV isolation', source_type='feishu', source_key='csv-isolation',
                                                credentials={'app_id': 'fixture', 'app_secret': 'fixture'})
        preview = preview_people_sync(source, SnapshotAdapter(source, []))
        apply_people_sync(source, preview)
        self.assertTrue(People.objects.get(employee_id='CSV-1').is_active)

    def test_final_expired_delivery_refreshes_event(self):
        from net.alerts.service import _claim_deliveries
        task = enqueue_task(self.profile, [self.server.pk], 'manual')
        claim_next_task('evidence', 60)
        with patch('requests.get', side_effect=requests.HTTPError('offline')):
            execute_target(task.target_runs.get(), worker_id='evidence')
        from net.inspections.queue import finish_task
        from net.alerts.task_summaries import process_task_summary
        finish_task(task.pk, 'evidence')
        event = process_task_summary(task)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, 'summary')
        AlertEvent.objects.filter(pk=event.pk).update(status='sending')
        channel = AlertChannel.objects.create(name='expired', channel_type='feishu')
        AlertDelivery.objects.create(event=event, channel=channel, status='sending', attempt_count=3,
            max_attempts=3, lease_token='old', lease_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(_claim_deliveries(), [])
        event.refresh_from_db()
        self.assertEqual(event.status, 'failed')
        from net.alerts.service import _finish_delivery
        from net.alerts.base import DeliveryResult
        expired = event.deliveries.get()
        _finish_delivery(expired.pk, 'old', DeliveryResult(True, 'late stale success', False))
        expired.refresh_from_db()
        self.assertEqual(expired.status, 'failed')
        other = AlertChannel.objects.create(name='already sent', channel_type='feishu')
        AlertDelivery.objects.create(event=event, channel=other, status='sent', delivered_at=timezone.now())
        AlertDelivery.objects.filter(pk=expired.pk).update(status='sending', lease_token='old',
            lease_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(_claim_deliveries(), [])
        event.refresh_from_db()
        self.assertEqual(event.status, 'partial')

    def test_live_progress_counts_finished_targets_without_double_counting(self):
        other = Server.objects.create(name='pending', ip='192.0.2.23', server_type='windows')
        task = enqueue_task(self.profile, [self.server.pk, other.pk], 'manual')
        claim_next_task('evidence', 60)
        target = task.target_runs.get(target_id=str(self.server.pk))
        with patch('requests.get', return_value=self.response({'cpu': {'usage_percent': 10}})):
            execute_target(target, worker_id='evidence')
            execute_target(target, worker_id='evidence')
        response = self.client.get(reverse('task_detail', args=[task.pk]))
        task.refresh_from_db()
        self.assertEqual(task.completed_targets, 1)
        self.assertEqual(response.context['task'].completed_targets, 1)
        self.assertEqual(response.context['task'].successful_targets, 1)

    def test_network_rejected_or_incomplete_output_is_not_success(self):
        from net.models import Network_Device
        from net.infrastructure.ssh_collectors import collect_network_ssh
        device = Network_Device(vendor='cisco', ip='192.0.2.24')
        for output in ('show processes cpu\nswitch#', '% Invalid input\nswitch#', 'CPU usage: 20%'):
            with self.subTest(output=output), patch('net.infrastructure.ssh_collectors._connect_network') as connect:
                connect.return_value.find_prompt.return_value = 'switch#'
                connect.return_value.send_command.return_value = output
                self.assertEqual(collect_network_ssh(device, selected_items=['cpu']).status, 'failed')
        with patch('net.infrastructure.ssh_collectors._connect_network') as connect:
            connect.return_value.find_prompt.return_value = 'switch#'
            connect.return_value.send_command.return_value = 'CPU usage: 20%\nswitch#'
            self.assertEqual(collect_network_ssh(device, selected_items=['cpu']).data['cpu']['usage_percent'], 20)
