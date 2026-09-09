"""End-to-end contracts for the infrastructure task Worker."""

import json
import threading
import time
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from cryptography.fernet import Fernet
from django.urls import reverse

from tests.devices.pc.helpers import create_log_file

from net.models import (
    ComputerAnalysisProfile,
    ComputerLogFile,
    Error_Server,
    InspectionProfile,
    Network_Device,
    Network_Device_Inspection,
    Server,
    Server_Inspection,
    TaskRun,
)
from net.infrastructure.collection import CollectionResult
from net.inspections.queue import enqueue_task
from net.inspections.queue import claim_next_task
from net.inspections.executor import execute_target
from net.inspections.worker import TaskWorker


class InfrastructureExecutorTests(TestCase):
    @patch('net.inspections.executor.collect_linux_ssh')
    def test_lease_loss_during_insert_rolls_back_result_and_progress(self, collect):
        from django.db.models.signals import post_save
        guard = threading.Event()
        target, task = self._claimed_target()
        collect.return_value = CollectionResult(True, 'success', data={'cpu': {'usage_percent': 20}})
        def lose_lease(sender, **kwargs):
            guard.set()
        post_save.connect(lose_lease, sender=Server_Inspection)
        try:
            outcome = execute_target(target, worker_id='executor-test', lease_guard=guard)
        finally:
            post_save.disconnect(lose_lease, sender=Server_Inspection)
        self.assertTrue(outcome.stale)
        self.assertFalse(Server_Inspection.objects.exists())
        self.assertFalse(Error_Server.objects.exists())
        target.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(target.status, 'running')
        self.assertEqual(task.completed_targets, 0)

    def setUp(self):
        self.profile = InspectionProfile.objects.create(
            name='服务器执行配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
            timeout_seconds=10,
            concurrent_workers=2,
        )
        self.server = Server.objects.create(
            name='LINUX-01',
            ip='192.0.2.110',
            server_type='linux',
            username='reader',
            password='snapshot-secret',
        )

    def _claimed_target(self):
        task = enqueue_task(
            self.profile,
            [self.server.pk],
            TaskRun.Source.MANUAL,
        )
        claimed = claim_next_task('executor-test', 30)
        return claimed.target_runs.get(), claimed

    @patch('net.inspections.executor.collect_windows_http')
    @patch('net.inspections.executor.collect_linux_ssh')
    def test_execute_target_uses_immutable_snapshot_and_selected_fields(
        self, linux_collect, windows_collect,
    ):
        target, _task = self._claimed_target()
        # Non-connection edits preserve the queued target and selected fields.
        # Connection changes are rejected by test_connection_edit_guard.
        self.server.name = 'renamed after enqueue'
        self.server.save(update_fields=['name'])
        linux_collect.return_value = CollectionResult(
            True,
            'success',
            data={'cpu': {'usage_percent': 22}, 'memory': {'used_percent': 91}},
            raw={'cpu': {'output': 'ok', 'password': 'must-not-persist'}, 'logs': 'unselected'},
            duration_ms=14,
        )
        windows_collect.return_value = CollectionResult(False, 'failed', 'wrong dispatch')

        outcome = execute_target(target, worker_id='executor-test')

        self.assertEqual(outcome.status, TaskRun.Status.SUCCESS)
        inspection = Server_Inspection.objects.get()
        self.assertEqual({key: value for key, value in inspection.details.items() if key not in {'issue_findings', 'normal_issue_items'}}, {'cpu': {'usage_percent': 22}})
        self.assertEqual(inspection.details['issue_findings'], [])
        self.assertEqual(inspection.raw_output, {'cpu': {'output': 'ok', 'password': '[REDACTED]'}})
        self.assertEqual(inspection.task_target_id, target.pk)
        target.refresh_from_db()
        self.assertEqual(target.result_type, 'server_inspection')
        self.assertEqual(target.result_id, str(inspection.pk))
        from net.inspections.result_storage import expanded_result_snapshot
        self.assertNotIn('details', target.result_snapshot)
        self.assertEqual(expanded_result_snapshot(target)['details']['cpu'], {'usage_percent': 22})
        self.assertEqual(expanded_result_snapshot(target)['details']['issue_findings'], [])
        self.assertNotIn('raw_output', target.result_snapshot)


class TaskWorkerTests(TransactionTestCase):
    def _profile(self, *, concurrent_workers=2):
        return InspectionProfile.objects.create(
            name=f'并发配置-{concurrent_workers}-{InspectionProfile.objects.count()}',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
            timeout_seconds=10,
            concurrent_workers=concurrent_workers,
        )

    def _server(self, suffix):
        return Server.objects.create(
            name=f'SRV-{suffix}',
            ip=f'192.0.2.{120 + suffix}',
            server_type='linux',
            username='reader',
            password='worker-secret',
        )

    @patch('net.devices.network.collector.collect_network_ssh')
    @patch('net.devices.network.collector.collect_network_snmp')
    @override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_worker_combines_hybrid_results_with_frozen_public_settings_and_live_secrets(
        self, snmp_collect, ssh_collect,
    ):
        profile = InspectionProfile.objects.create(
            name='混合巡检',
            device_type=InspectionProfile.DeviceType.NETWORK_DEVICE,
            selected_items=['cpu', 'memory', 'logs'],
            timeout_seconds=9,
            concurrent_workers=1,
        )
        device = Network_Device.objects.create(
            device_name='HYBRID-01',
            ip='192.0.2.150',
            vendor='cisco',
            connection_type='hybrid',
            port=2222,
            username='queued-ssh-user',
            password='queued-ssh-password',
            snmp_version='v3',
            snmp_port=1161,
            snmp_community='queued-community-secret',
            snmp_security_level='authPriv',
            snmp_username='public-snmp-user',
            snmp_auth_protocol='sha256',
            snmp_auth_password='queued-auth-password',
            snmp_priv_protocol='aes128',
            snmp_priv_password='queued-priv-password',
            snmp_context_name='tenant-a',
            snmp_retries=3,
        )
        task = enqueue_task(profile, [device.pk], TaskRun.Source.MANUAL)
        target = task.target_runs.get()
        queued_snapshot = json.dumps(
            [task.profile_snapshot, target.target_snapshot], ensure_ascii=False,
        )
        for secret in (
            'queued-ssh-user', 'queued-ssh-password', 'queued-auth-password',
            'queued-priv-password', 'queued-community-secret',
        ):
            self.assertNotIn(secret, queued_snapshot)
        self.assertEqual(target.target_snapshot['connection_type'], 'hybrid')
        self.assertEqual(target.target_snapshot['snmp_port'], 1161)
        self.assertEqual(target.target_snapshot['snmp_context_name'], 'tenant-a')

        device.username = 'live-ssh-user'
        device.password = 'live-ssh-password'
        device.snmp_community = 'live-community-secret'
        device.snmp_auth_password = 'live-auth-password'
        device.snmp_priv_password = 'live-priv-password'
        device.device_name = 'renamed after enqueue'
        device.save()
        seen = {}

        def snmp_result(asset, timeout, selected_items=None):
            seen['snmp'] = (
                asset.username, asset.password, asset.snmp_auth_password,
                asset.snmp_priv_password, asset.snmp_community, asset.snmp_port, timeout,
                list(selected_items or []),
            )
            return CollectionResult(
                True,
                'success',
                data={
                    'cpu': {'usage_percent': 17, 'notice': 'live-auth-password'},
                    'memory': {'usage_percent': 41},
                },
                raw={
                    'cpu': {'1.3.6.1': 'live-auth-password'},
                    'memory': {'1.3.6.2': 'memory evidence'},
                    'vlan_status': {'1.3.6.3': 'unselected SNMP evidence'},
                },
            )

        def ssh_result(asset, timeout, selected_items=None):
            seen['ssh'] = (
                asset.username, asset.password, timeout, list(selected_items or []),
            )
            return CollectionResult(
                True,
                'success',
                data={'logs': ['live-priv-password'], 'config_info': {
                    'status': 'success', 'vendor': 'cisco', 'format': 'text',
                    'scope': 'running-config', 'complete': True,
                    'content': 'hostname edge\nusername admin secret live-priv-password\nend\n',
                }},
                raw={
                    'cpu': 'SSH collision evidence',
                    'show logging | last 100': 'SSH log evidence',
                    'show vlan brief': 'unselected SSH evidence',
                },
            )

        snmp_collect.side_effect = snmp_result
        ssh_collect.side_effect = ssh_result

        TaskWorker(worker_id='hybrid-worker', threads=1, lease_seconds=10).run_once()

        inspection = Network_Device_Inspection.objects.get()
        self.assertEqual(inspection.status, 'success')
        self.assertEqual(set(inspection.details) - {'issue_findings', 'normal_issue_items'}, {'cpu', 'memory', 'logs', 'config_info'})
        self.assertEqual(inspection.details['config_info']['status'], 'success')
        self.assertEqual(inspection.raw_output, {
            'snmp:cpu': {'1.3.6.1': '[REDACTED]'},
            'ssh:cpu': 'SSH collision evidence',
            'memory': {'1.3.6.2': 'memory evidence'},
            'show logging | last 100': 'SSH log evidence',
        })
        self.assertEqual(seen['snmp'], (
            'live-ssh-user', 'live-ssh-password', 'live-auth-password',
            'live-priv-password', 'live-community-secret', 1161, 9,
            ['cpu', 'memory'],
        ))
        self.assertEqual(seen['ssh'], (
            'live-ssh-user', 'live-ssh-password', 9, ['logs', 'config_info'],
        ))
        persisted = json.dumps([
            inspection.details,
            inspection.raw_output,
            task.target_runs.get().result_snapshot,
        ], ensure_ascii=False)
        for secret in (
            'live-ssh-user', 'live-ssh-password', 'live-auth-password',
            'live-priv-password', 'live-community-secret',
        ):
            self.assertNotIn(secret, persisted)

    @patch('net.devices.network.collector.collect_network_ssh')
    @patch('net.devices.network.collector.collect_network_snmp')
    def test_worker_persists_partial_when_snmp_fails_and_ssh_succeeds(
        self, snmp_collect, ssh_collect,
    ):
        profile = InspectionProfile.objects.create(
            name='混合部分成功',
            device_type=InspectionProfile.DeviceType.NETWORK_DEVICE,
            selected_items=['cpu', 'logs'],
            concurrent_workers=1,
        )
        device = Network_Device.objects.create(
            ip='192.0.2.151',
            connection_type='hybrid',
            username='reader',
            password='ssh-secret',
            snmp_community='snmp-secret',
        )
        enqueue_task(profile, [device.pk], TaskRun.Source.MANUAL)
        snmp_collect.return_value = CollectionResult(False, 'failed', 'SNMP unavailable')
        ssh_collect.return_value = CollectionResult(
            True, 'success', data={'logs': ['accepted']},
        )

        TaskWorker(worker_id='hybrid-partial', threads=1, lease_seconds=10).run_once()

        inspection = Network_Device_Inspection.objects.get()
        self.assertEqual(inspection.status, 'partial')
        self.assertEqual({key: value for key, value in inspection.details.items() if key not in {'issue_findings', 'normal_issue_items', 'config_info'}}, {'logs': ['accepted']})
        self.assertEqual(inspection.details['config_info']['status'], 'failed')
        self.assertEqual(inspection.details['issue_findings'][0]['rule_key'], 'missing.cpu')
        run = TaskRun.objects.get()
        run.refresh_from_db()
        self.assertEqual(run.status, TaskRun.Status.PARTIAL)
        self.assertEqual(run.target_runs.get().status, TaskRun.Status.PARTIAL)

    @patch('net.inspections.executor.collect_linux_ssh')
    def test_worker_keeps_parent_failed_when_every_target_fails(self, collect):
        profile = self._profile(concurrent_workers=1)
        server = self._server(10)
        enqueue_task(profile, [server.pk], TaskRun.Source.MANUAL)
        collect.return_value = CollectionResult(False, 'failed', 'unreachable')

        TaskWorker(worker_id='all-failed-worker', threads=1, lease_seconds=10).run_once()

        run = TaskRun.objects.get()
        run.refresh_from_db()
        self.assertEqual(run.status, TaskRun.Status.FAILED)

    @patch('net.inspections.executor.collect_linux_ssh')
    def test_worker_isolates_target_failure_and_finishes_partial_task(self, collect):
        profile = self._profile(concurrent_workers=1)
        first, second = self._server(1), self._server(2)
        enqueue_task(profile, [first.pk, second.pk], TaskRun.Source.MANUAL)

        def collect_one(asset, timeout, selected_items=None):
            if asset.ip.endswith('.122'):
                raise RuntimeError('collector crashed')
            return CollectionResult(True, 'success', data={'cpu': {'usage_percent': 8}})

        collect.side_effect = collect_one

        self.assertTrue(TaskWorker(worker_id='partial-worker', threads=4, lease_seconds=10).run_once())

        task = TaskRun.objects.get()
        self.assertEqual(task.status, TaskRun.Status.PARTIAL)
        self.assertEqual(task.successful_targets, 1)
        self.assertEqual(task.failed_targets, 1)
        self.assertEqual(Server_Inspection.objects.count(), 2)
        self.assertEqual(Error_Server.objects.count(), 1)
        self.assertEqual(task.target_runs.filter(status=TaskRun.Status.FAILED).count(), 1)

    @patch('net.inspections.executor.collect_linux_ssh')
    def test_worker_honors_manual_concurrency_override_within_global_cap(self, collect):
        profile = self._profile(concurrent_workers=3)
        first, second = self._server(3), self._server(4)
        enqueue_task(
            profile,
            [first.pk, second.pk],
            TaskRun.Source.MANUAL,
            overrides={'parameters': {'concurrent_workers': 1}},
        )
        active = 0
        peak = 0
        lock = threading.Lock()

        def slow_collect(_asset, _timeout, selected_items=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.05)
                return CollectionResult(True, 'success', data={'cpu': {'usage_percent': 10}})
            finally:
                with lock:
                    active -= 1

        collect.side_effect = slow_collect

        TaskWorker(worker_id='bounded-worker', threads=4, lease_seconds=10).run_once()

        self.assertEqual(peak, 1)
        self.assertEqual(TaskRun.objects.get().status, TaskRun.Status.SUCCESS)

    @patch('net.inspections.executor.collect_linux_ssh')
    def test_worker_runs_collectors_concurrently_up_to_global_cap(self, collect):
        profile = self._profile(concurrent_workers=2)
        first, second = self._server(7), self._server(8)
        enqueue_task(profile, [first.pk, second.pk], TaskRun.Source.MANUAL)
        active = 0
        peak = 0
        lock = threading.Lock()

        def slow_collect(_asset, _timeout, selected_items=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.08)
                return CollectionResult(True, 'success', data={'cpu': {'usage_percent': 10}})
            finally:
                with lock:
                    active -= 1

        collect.side_effect = slow_collect

        TaskWorker(worker_id='global-cap-worker', threads=2, lease_seconds=10).run_once()

        self.assertEqual(peak, 2)
        self.assertEqual(TaskRun.objects.get().status, TaskRun.Status.SUCCESS)

    def test_worker_claims_computer_analysis_tasks_and_isolates_invalid_log_evidence(self):
        profile = ComputerAnalysisProfile.objects.create(
            name='尚未启用的分析器',
            analysis_items=['activation'],
        )
        log_file = create_log_file(
            source_path='C:/logs/pc.json',
            modified_at='2026-08-31T00:00:00+08:00',
            content_hash='a' * 64,
            import_status='success',
        )
        task = enqueue_task(profile, [log_file.pk], TaskRun.Source.MANUAL)

        self.assertTrue(TaskWorker(worker_id='computer-aware', threads=1, lease_seconds=10).run_once())

        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.FAILED)
        self.assertEqual(task.target_runs.get().status, TaskRun.Status.FAILED)

    @patch('net.inspections.executor.collect_linux_ssh')
    def test_collector_exception_never_persists_secret_text(self, collect):
        profile = self._profile(concurrent_workers=1)
        server = self._server(9)
        enqueue_task(profile, [server.pk], TaskRun.Source.MANUAL)
        collect.side_effect = RuntimeError('api_token=super-secret-token')

        TaskWorker(worker_id='redaction-worker', threads=1, lease_seconds=10).run_once()

        target = TaskRun.objects.get().target_runs.get()
        inspection = Server_Inspection.objects.get()
        self.assertNotIn('super-secret-token', target.error_message)
        self.assertNotIn('super-secret-token', inspection.summary)
        self.assertNotIn('super-secret-token', Error_Server.objects.get().error_message['详细信息'])

    @patch('net.inspections.worker.renew_lease', wraps=__import__('net.inspections.queue', fromlist=['renew_lease']).renew_lease)
    @patch('net.inspections.executor.collect_linux_ssh')
    def test_worker_renews_lease_until_long_collector_finishes(self, collect, renew):
        profile = self._profile(concurrent_workers=1)
        server = self._server(5)
        enqueue_task(profile, [server.pk], TaskRun.Source.MANUAL)

        def slow_collect(_asset, _timeout, selected_items=None):
            time.sleep(1.1)
            return CollectionResult(True, 'success', data={'cpu': {'usage_percent': 12}})

        collect.side_effect = slow_collect

        TaskWorker(worker_id='heartbeat-worker', threads=1, lease_seconds=1).run_once()

        self.assertGreaterEqual(renew.call_count, 1)
        self.assertEqual(TaskRun.objects.get().status, TaskRun.Status.SUCCESS)

    @patch('net.inspections.executor.collect_linux_ssh')
    @patch('net.inspections.worker.renew_lease', return_value=False)
    def test_stale_worker_does_not_persist_collector_result(self, renew, collect):
        profile = self._profile(concurrent_workers=1)
        server = self._server(6)
        enqueue_task(profile, [server.pk], TaskRun.Source.MANUAL)
        def slow_collect(_asset, _timeout, selected_items=None):
            time.sleep(0.45)
            return CollectionResult(True, 'success', data={'cpu': {'usage_percent': 5}})

        collect.side_effect = slow_collect

        TaskWorker(worker_id='stale-worker', threads=1, lease_seconds=1).run_once()

        self.assertGreaterEqual(renew.call_count, 1)
        self.assertEqual(Server_Inspection.objects.count(), 0)
        task = TaskRun.objects.get()
        self.assertEqual(task.status, TaskRun.Status.RUNNING)
        self.assertEqual(task.target_runs.get().status, TaskRun.Status.RUNNING)


class WorkerCommandAndLegacyEntryTests(TestCase):
    def test_once_command_exits_when_queue_is_empty(self):
        output = StringIO()

        call_command('run_task_worker', '--once', stdout=output)

        self.assertIn('没有可执行任务', output.getvalue())

    def test_legacy_command_and_web_entry_only_enqueue_work(self):
        device = Network_Device.objects.create(
            device_name='SW-QUEUE', ip='192.0.2.140', username='reader', password='secret',
        )
        output = StringIO()

        call_command('run_network_checks', asset_type='networks', asset_id=str(device.pk), stdout=output)

        self.assertIn('已创建', output.getvalue())
        self.assertEqual(Network_Device_Inspection.objects.count(), 0)
        self.assertEqual(TaskRun.objects.filter(status=TaskRun.Status.QUEUED).count(), 1)

    def test_web_manual_entry_enqueues_without_running_collectors(self):
        from tests.auth import login_admin
        login_admin(self.client)
        device = Network_Device.objects.create(
            device_name='SW-WEB-QUEUE', ip='192.0.2.141', username='reader', password='secret',
        )

        response = self.client.post(reverse('run_infrastructure_inspection'), {
            'asset_type': 'networks',
            'asset_id': str(device.pk),
        })

        self.assertRedirects(response, reverse('item_list', args=['networks']))
        self.assertEqual(Network_Device_Inspection.objects.count(), 0)
        self.assertEqual(TaskRun.objects.filter(status=TaskRun.Status.QUEUED).count(), 1)
