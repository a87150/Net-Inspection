"""Regression coverage for Task 4 review findings; external transports only are faked."""

import json
import threading
from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
import requests

from django.test import TestCase, TransactionTestCase, override_settings
from cryptography.fernet import Fernet
from django.db import close_old_connections, connections
from django.core.management import call_command
from django.utils import timezone

from net.models import InspectionProfile, SecurityDevice, Network_Device, Server, Error_Server, TaskRun, Server_Inspection
from net.inspections.queue import enqueue_task, claim_next_task, finish_task, recover_expired_tasks
from net.inspections.executor import execute_target
from net.infrastructure.collection import CollectionResult
from net.infrastructure.sanitization import configuration_secrets, sanitize
from net.inspections.worker import TaskWorker


class ObservedStop(threading.Event):
    def __init__(self):
        super().__init__()
        self.observed = threading.Event()

    def is_set(self):
        stopped = super().is_set()
        if stopped and threading.current_thread().name == 'test-worker-main':
            self.observed.set()
        return stopped


def _wait_for(predicate, timeout):
    deadline = __import__('time').monotonic() + timeout
    while __import__('time').monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.01)
    return predicate()


class WorkerLifecycleTests(TransactionTestCase):
    def make_task(self, count=2, concurrency=1):
        profile = InspectionProfile.objects.create(
            name='lifecycle', device_type='server', selected_items=['cpu'], concurrent_workers=concurrency,
        )
        assets = [Server.objects.create(ip=f'192.0.2.{230 + number}', username='reader', password='password')
                  for number in range(count)]
        return enqueue_task(profile, [asset.pk for asset in assets], 'manual')

    def test_maintenance_failure_still_closes_main_thread_connections(self):
        worker = TaskWorker(threads=1)
        real_close = connections.close_all
        close_calls = []

        def close_connections():
            close_calls.append(True)
            return real_close()

        with patch('net.inspections.worker.recover_expired_tasks'), \
                patch('net.inspections.worker.reconcile_pending_analysis_handoffs', return_value=[]), \
                patch('net.inspections.worker.enqueue_due_schedules'), \
                patch('net.inspections.worker.claim_next_task', return_value=None), \
                patch.object(worker, '_maintenance_cycle', side_effect=RuntimeError('maintenance failure')), \
                patch.object(connections, 'close_all', side_effect=close_connections):
            with self.assertRaises(RuntimeError):
                worker.run_once()

        self.assertTrue(close_calls)

    def test_maintenance_can_block_on_delivery_without_stopping_task_lease_heartbeats(self):
        task = self.make_task(count=1)
        claimed = claim_next_task('maintenance-heartbeat', 1)
        entered, release = threading.Event(), threading.Event()
        stop, maintenance_stop = threading.Event(), threading.Event()
        renewals = []
        worker = TaskWorker(worker_id='maintenance-heartbeat', threads=1, lease_seconds=1, poll_seconds=0.01)

        def blocking_target(*_args, **_kwargs):
            entered.set()
            release.wait(3)

        def blocking_delivery(**_kwargs):
            entered.set()
            release.wait(3)
            return []

        with patch('net.inspections.worker.execute_target', side_effect=blocking_target), \
                patch('net.inspections.worker.reconcile_pending_analysis_handoffs', return_value=[]), \
                patch('net.inspections.worker.enqueue_due_schedules', return_value=[]), \
                patch('net.alerts.service.reconcile_terminal_targets'), \
                patch('net.alerts.task_summaries.reconcile_terminal_tasks'), \
                patch('net.alerts.service.deliver_due_alerts', side_effect=blocking_delivery), \
                patch('net.inspections.worker.renew_lease', side_effect=lambda *_args: renewals.append(1) or True):
            maintainer = threading.Thread(target=worker._maintenance_loop, args=(stop, maintenance_stop))
            runner = threading.Thread(target=worker._execute_claimed_task, args=(claimed, stop))
            maintainer.start()
            runner.start()
            self.assertTrue(entered.wait(2))
            # The delivery remains blocked; the target wait loop must still renew.
            self.assertTrue(_wait_for(lambda: bool(renewals), timeout=2))
            release.set()
            maintenance_stop.set()
            runner.join(4)
            maintainer.join(4)
        self.assertFalse(runner.is_alive())
        self.assertFalse(maintainer.is_alive())
        self.assertTrue(renewals)

    def test_each_target_thread_closes_its_connection_on_success_and_exception(self):
        task = self.make_task()
        events = []
        real_close = connections.close_all

        def old_connections():
            events.append((threading.get_ident(), 'refresh'))
            close_old_connections()

        def close_connections():
            events.append((threading.get_ident(), 'close'))
            real_close()

        def execution(target, **kwargs):
            events.append((threading.get_ident(), 'execute'))
            result = execute_target(target, **kwargs)
            if len([event for _, event in events if event == 'execute']) == 2:
                raise RuntimeError('wrapper boundary error')
            return result

        with patch('net.inspections.worker.execute_target', side_effect=execution), \
                patch('net.inspections.executor.collect_linux_ssh', return_value=CollectionResult(True, 'success')), \
                patch('net.inspections.worker.close_old_connections', side_effect=old_connections, create=True), \
                patch.object(connections, 'close_all', side_effect=close_connections):
            TaskWorker(threads=1).run_once()
        task.refresh_from_db()
        self.assertEqual(task.status, 'success')
        thread_ids = {identity for identity, event in events if event == 'execute'}
        self.assertNotIn(threading.get_ident(), thread_ids)
        for identity in thread_ids:
            ordered = [event for tid, event in events if tid == identity]
            self.assertEqual(ordered, ['refresh', 'execute', 'refresh', 'close'] * 2)
        self.assertIn((threading.get_ident(), 'close'), events)

    def stop_during_collection(self, *, command=False, queued_future=False, reclaim=False):
        task = self.make_task(concurrency=2 if queued_future else 1)
        stop = ObservedStop()
        entered, release = threading.Event(), threading.Event()
        calls, errors = [], []
        futures = []
        two_submitted = threading.Event()
        pending_cancelled = threading.Event()

        def constrained_pool(**kwargs):
            # A real one-thread pool lets the test deterministically hold a second
            # submitted Future unstarted until cancellation, independent of timing.
            pool = ThreadPoolExecutor(max_workers=1)
            submit = pool.submit

            def capture(*args, **options):
                future = submit(*args, **options)
                futures.append(future)
                if len(futures) == 2:
                    future.add_done_callback(lambda done: pending_cancelled.set() if done.cancelled() else None)
                    two_submitted.set()
                return future

            pool.submit = capture
            return pool

        def blocking(asset, timeout, **kwargs):
            calls.append(asset.ip)
            entered.set()
            if not release.wait(5):
                raise TimeoutError('bounded fake collector')
            return CollectionResult(True, 'success', data={'cpu': {'usage': 1}})

        def run():
            try:
                if command:
                    call_command('run_task_worker', '--once', '--threads', '1', stdout=StringIO())
                else:
                    TaskWorker(threads=2 if queued_future else 1).run_forever(stop)
            except BaseException as exc:
                errors.append(exc)

        with patch('net.inspections.executor.collect_linux_ssh', side_effect=blocking), \
                patch('net.management.commands.run_task_worker.Event', return_value=stop, create=True), \
                patch('net.inspections.worker.ThreadPoolExecutor', side_effect=constrained_pool):
            thread = threading.Thread(target=run, name='test-worker-main')
            thread.start()
            try:
                self.assertTrue(entered.wait(3), errors)
                if queued_future:
                    self.assertTrue(two_submitted.wait(2))
                stop.set()
                self.assertTrue(stop.observed.wait(2), 'stop event not checked during collection')
                if queued_future:
                    self.assertTrue(pending_cancelled.wait(2), 'pending Future was not cancelled')
                if reclaim:
                    task.refresh_from_db()
                    recovery_time = task.lease_expires_at + timedelta(seconds=1)
                    recover_expired_tasks(now=recovery_time)
                    self.assertEqual(claim_next_task('replacement-owner', 30, now=recovery_time).pk, task.pk)
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive(), 'bounded collector did not finish')
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1)
        self.assertFalse(Server_Inspection.objects.exists())
        task.refresh_from_db()
        self.assertEqual(task.status, 'running')
        if queued_future:
            self.assertEqual(len(futures), 2)
            self.assertTrue(futures[1].cancelled())
        if reclaim:
            self.assertEqual(task.worker_id, 'replacement-owner')
            self.assertEqual(task.target_runs.filter(status='queued').count(), 2)
            return
        self.assertEqual(task.target_runs.filter(status='queued').count(), 1)
        recover_expired_tasks(now=task.lease_expires_at + timedelta(seconds=1))
        task.refresh_from_db()
        self.assertEqual(task.status, 'queued')
        self.assertEqual(task.target_runs.filter(status='queued').count(), 2)

    def test_service_stop_while_collector_is_blocked_hands_off_to_lease_recovery(self):
        self.stop_during_collection()

    def test_once_command_stop_while_collector_is_blocked_does_not_start_more_targets(self):
        self.stop_during_collection(command=True)

    def test_stop_cancels_a_real_unstarted_future(self):
        self.stop_during_collection(queued_future=True)

    def test_stop_fences_old_collector_after_lease_is_reclaimed(self):
        self.stop_during_collection(reclaim=True)

    def test_once_terminal_only_recovers_then_exits_without_collection(self):
        self.make_task(count=1)
        task = claim_next_task('previous', 30)
        target = task.target_runs.get()
        target.status = 'success'
        target.finished_at = timezone.now()
        target.save()
        task.lease_expires_at = timezone.now() - timedelta(seconds=1)
        task.save()
        output = StringIO()
        call_command('run_task_worker', '--once', stdout=output)
        task.refresh_from_db()
        self.assertEqual(task.status, 'success')
        self.assertIn('没有可执行任务', output.getvalue())
        self.assertFalse(Server_Inspection.objects.exists())

    def test_once_cancelled_parent_never_executes_its_queued_targets(self):
        task = self.make_task(count=1)
        task.status = 'cancelled'
        task.finished_at = timezone.now()
        task.save()
        call_command('run_task_worker', '--once', stdout=StringIO())
        self.assertEqual(task.target_runs.get().status, 'queued')
        self.assertFalse(Server_Inspection.objects.exists())


class SanitizerPersistenceTests(TestCase):
    def test_network_snmp_secrets_participate_in_runtime_sanitization(self):
        device = Network_Device.objects.create(
            ip='192.0.2.209',
            username='ssh-user-secret',
            password='ssh-password-secret',
            snmp_community='community-secret',
            snmp_auth_password='auth-password-secret',
            snmp_priv_password='priv-password-secret',
        )
        secret_values = (
            device.username,
            device.password,
            device.snmp_community,
            device.snmp_auth_password,
            device.snmp_priv_password,
        )

        scrubbed = sanitize(' '.join(secret_values), secrets=configuration_secrets())

        for secret in secret_values:
            self.assertNotIn(secret, scrubbed)

    def test_nested_and_exception_credentials_never_reach_results_or_outcomes(self):
        text = ('Authorization: Bearer bearer-original; Basic YWRtaW46cGFzcw== '
                'https://url-user:url-pass@host/path?api_key=query-original&ok=1 '
                'https://host/?auth=auth-original&key=plain-key-original&signature=signed-original&api%5Fkey=encoded-original '
                'password="quoted original secret" access_token=token-original')
        payload = {
            'response': [text, {'nested': [text, 12, None], 'apiPassword': 'key-original'}],
            'Authorization: Bearer key-bearer-original': 'value',
            'notice': 'the credential is live-password-original',
        }
        forbidden = ['bearer-original', 'YWRtaW46cGFzcw==', 'url-user', 'url-pass',
                     'query-original', 'quoted original secret', 'token-original',
                     'key-original', 'key-bearer-original', 'live-password-original',
                     'auth-original', 'plain-key-original', 'signed-original', 'encoded-original']
        for failed in (False, True):
            with self.subTest(exception=failed):
                asset = Server.objects.create(ip=f'192.0.2.{210 + int(failed)}', username='reader', password='live-password-original')
                profile = InspectionProfile.objects.create(name=str(failed), device_type='server', selected_items=['cpu'])
                enqueue_task(profile, [asset.pk], 'manual')
                task = claim_next_task('sanitize-worker', 30)
                target = task.target_runs.get()
                with patch('net.inspections.executor.collect_linux_ssh') as collector:
                    if failed:
                        collector.side_effect = RuntimeError(text + ' live-password-original')
                    else:
                        collector.return_value = CollectionResult(True, 'partial', text, {'cpu': payload}, {'cpu': payload})
                    outcome = execute_target(target, worker_id='sanitize-worker')
                finish_task(task.pk, 'sanitize-worker')
                target.refresh_from_db()
                task.refresh_from_db()
                record = asset.inspections.get()
                surfaces = [record.raw_output, record.details, record.summary,
                            Error_Server.objects.get(inspection=record).error_message,
                            target.error_message, target.result_snapshot, task.error_summary,
                            vars(outcome)]
                encoded = json.dumps(surfaces, ensure_ascii=False)
                for secret in forbidden:
                    self.assertNotIn(secret, encoded)
                if not failed:
                    self.assertIn('12', encoded)
                    self.assertNotIn('ok=1', encoded)  # Opaque query values are never public diagnostics.
                    self.assertIn('https://host/path', encoded)


class SelectedCollectionTests(TestCase):
    def assert_selected_details(self, record, expected):
        # Grading metadata is not collected evidence; reject every other extra key.
        metadata = {'issue_findings', 'normal_issue_items'}
        self.assertEqual({key: value for key, value in record.details.items()
                          if key not in metadata}, expected)
        self.assertLessEqual(
            {issue['analysis_item'] for issue in record.details['issue_findings']}
            | set(record.details['normal_issue_items']),
            set(expected) | {'inspection_collection'},
        )

    def execute(self, asset, kind, items):
        profile = InspectionProfile.objects.create(
            name='selected', device_type=kind, selected_items=items,
        )
        enqueue_task(profile, [asset.pk], 'manual')
        task = claim_next_task('selection-worker', 30)
        target = task.target_runs.get()
        outcome = execute_target(target, worker_id='selection-worker')
        self.assertEqual(outcome.status, 'success', outcome.error_message)
        return asset.inspections.get()

    @patch('net.infrastructure.ssh_collectors._connect')
    def test_linux_cpu_only_sends_cpu_command_and_retains_cpu_raw(self, connect):
        commands = []

        def command(text, **kwargs):
            commands.append(text)
            stdout = BytesIO(b'CPU selected')
            stdout.channel = Mock()
            stdout.channel.recv_exit_status.return_value = 0
            return None, stdout, BytesIO(b'')

        connect.return_value.exec_command.side_effect = command
        asset = Server.objects.create(ip='192.0.2.201', username='reader', password='secret')
        record = self.execute(asset, 'server', ['cpu'])
        self.assertEqual(len(commands), 1)
        self.assertIn('lscpu', commands[0])
        self.assertEqual(record.raw_output, {'cpu': 'CPU selected'})
        self.assert_selected_details(record, {'cpu': {'raw': 'CPU selected'}})
        self.assertEqual([(issue['analysis_item'], issue['severity'])
                          for issue in record.details['issue_findings']], [('cpu', 'info')])
        self.assertEqual(record.details['normal_issue_items'], ['inspection_collection'])

    @patch('net.infrastructure.ssh_collectors._connect_network')
    @override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_network_cpu_only_never_sends_logs_services_or_vlan(self, connect):
        connect.return_value.find_prompt.return_value = 'switch#'
        connect.return_value.send_command.return_value = 'CPU usage: 20%\nswitch#'
        asset = Network_Device.objects.create(
            ip='192.0.2.202', vendor='cisco', username='reader', password='secret',
        )
        from net.devices.configuration_backups import store_configuration_backup
        backup = store_configuration_backup(asset, {
            'status': 'success', 'vendor': 'cisco', 'format': 'text', 'complete': True,
            'scope': 'running-config', 'content': 'hostname switch\nend\n',
        })
        record = self.execute(asset, 'network_device', ['cpu'])
        sent = [call.args[0] for call in connect.return_value.send_command.call_args_list]
        self.assertEqual(sent, ['terminal length 0', 'show processes cpu'])
        self.assertEqual(record.raw_output['show processes cpu'], 'CPU usage: 20%')
        self.assertEqual(record.raw_output['config_info']['backup_id'], str(backup.pk))
        self.assertEqual(record.details['cpu'], {'usage_percent': 20})
        self.assertEqual(record.details['config_info']['backup_id'], str(backup.pk))
        self.assertEqual(set(record.details), {'cpu', 'config_info', 'issue_findings', 'normal_issue_items'})
        self.assertEqual(record.details['issue_findings'], [])
        self.assertEqual(set(record.details['normal_issue_items']), {'inspection_collection', 'cpu', 'config_info'})

    @patch('net.infrastructure.http_collectors.requests.get')
    def test_windows_cpu_request_and_raw_exclude_unselected_fields(self, get):
        response = Mock()
        response.headers = {'content-type': 'application/json'}
        response.json.return_value = {
            'cpu': {'usage_percent': 12}, 'logs': ['PRIVATE LOG'], 'services': ['PRIVATE SERVICE'],
        }
        get.return_value = response
        asset = Server.objects.create(ip='192.0.2.203', server_type='windows', api_url='http://192.0.2.203/inspection?fields=logs')
        record = self.execute(asset, 'server', ['cpu'])
        self.assertEqual(get.call_args.kwargs.get('params'), {'fields': 'cpu'})
        prepared = requests.Request('GET', get.call_args.args[0], params=get.call_args.kwargs['params']).prepare()
        self.assertEqual(parse_qs(urlsplit(prepared.url).query)['fields'], ['cpu'])
        self.assertEqual(record.raw_output, {'cpu': {'usage_percent': 12}})
        self.assertNotIn('PRIVATE', json.dumps(record.details))

    @patch('net.infrastructure.http_collectors.requests.get')
    def test_security_storage_request_and_raw_exclude_channels(self, get):
        response = Mock()
        response.headers = {'content-type': 'application/json'}
        response.json.return_value = {
            'storage': [{'status': 'normal'}], 'channels': ['PRIVATE CHANNEL'], 'logs': ['PRIVATE LOG'],
        }
        get.return_value = response
        asset = SecurityDevice.objects.create(ip='192.0.2.204', api_url='http://192.0.2.204/status')
        record = self.execute(asset, 'monitor', ['storage_status'])
        self.assertEqual(get.call_args.kwargs.get('params'), {'fields': 'storage_status'})
        self.assertEqual(record.raw_output, {'storage': [{'status': 'normal'}]})
        self.assert_selected_details(record, {'storage_status': [{'status': 'normal'}]})
        self.assertEqual(record.details['issue_findings'], [])
        self.assertEqual(set(record.details['normal_issue_items']), {'inspection_collection', 'storage_status'})
        self.assertNotIn('PRIVATE', json.dumps(record.details))
