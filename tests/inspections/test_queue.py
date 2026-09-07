from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from tests.devices.pc.helpers import create_log_file

from net.models import (
    ComputerAnalysisProfile,
    ComputerLogFile,
    InspectionProfile,
    Network_Device,
    Server,
    TaskRun,
)


class EnqueueTaskTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(
            name='服务器日常巡检',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['reachability', 'cpu'],
        )
        self.server = Server.objects.create(
            name='应用服务器',
            ip='192.0.2.41',
            server_type='linux',
            os='Ubuntu 24.04',
        )

    def test_enqueue_snapshots_server_target_and_profile_items(self):
        """Removing snapshot creation would lose the execution audit input."""
        try:
            from net.inspections.queue import enqueue_task
        except ImportError as exc:
            self.fail(f'队列服务尚未提供: {exc}')

        task = enqueue_task(
            self.profile,
            [self.server.pk],
            TaskRun.Source.MANUAL,
        )

        self.assertEqual(task.task_type, TaskRun.TaskType.INSPECTION)
        self.assertEqual(task.status, TaskRun.Status.QUEUED)
        self.assertEqual(task.total_targets, 1)
        self.assertEqual(task.selected_items_snapshot, ['reachability', 'cpu'])
        self.assertEqual(task.target_scope_snapshot, {
            'targets': [{
                'target_id': str(self.server.pk),
                'target_type': 'server',
            }],
        })
        target = task.target_runs.get()
        self.assertEqual(target.target_type, 'server')
        self.assertEqual(target.target_id, str(self.server.pk))
        self.assertEqual(target.target_snapshot['ip'], '192.0.2.41')
        self.assertEqual(target.target_snapshot['name'], '应用服务器')
        self.assertNotIn('password', target.target_snapshot)

    def test_enqueue_snapshots_public_snmp_settings_and_excludes_all_secrets(self):
        from net.inspections.queue import enqueue_task

        profile = InspectionProfile.objects.create(
            name='网络混合巡检',
            device_type=InspectionProfile.DeviceType.NETWORK_DEVICE,
            selected_items=['device_info'],
        )
        device = Network_Device.objects.create(
            device_name='边界路由器', ip='192.0.2.88', connection_type='hybrid',
            username='ssh-user', password='ssh-secret',
            snmp_version='v3', snmp_port=1161,
            snmp_security_level='authPriv', snmp_username='snmp-user',
            snmp_auth_protocol='sha256', snmp_auth_password='auth-secret',
            snmp_priv_protocol='aes128', snmp_priv_password='priv-secret',
            snmp_context_name='tenant-a', snmp_retries=5,
        )

        task = enqueue_task(profile, [device.pk], TaskRun.Source.MANUAL)
        snapshot = task.target_runs.get().target_snapshot

        self.assertEqual(snapshot, {
            'id': str(device.pk),
            'device_name': '边界路由器',
            'ip': '192.0.2.88',
            'device_type': None,
            'model': None,
            'vendor': None,
            'connection_type': 'hybrid',
            'port': 22,
            'snmp_version': 'v3',
            'snmp_port': 1161,
            'snmp_security_level': 'authPriv',
            'snmp_username': 'snmp-user',
            'snmp_auth_protocol': 'sha256',
            'snmp_priv_protocol': 'aes128',
            'snmp_context_name': 'tenant-a',
            'snmp_retries': 5,
        })

    def test_enqueue_rejects_profile_items_not_enabled_by_profile(self):
        """Removing profile-item validation would run an unconfigured check."""
        from net.inspections.queue import enqueue_task

        with self.assertRaises(ValidationError):
            enqueue_task(
                self.profile,
                [self.server.pk],
                TaskRun.Source.MANUAL,
                overrides={'selected_items': ['disk_usage']},
            )

        self.assertEqual(TaskRun.objects.count(), 0)

    def test_enqueue_rejects_duplicate_active_normalized_scope(self):
        """Dropping the active-scope guard would queue identical work twice."""
        from net.inspections.queue import enqueue_task

        enqueue_task(self.profile, [self.server.pk], TaskRun.Source.MANUAL)

        with self.assertRaises(ValidationError):
            enqueue_task(self.profile, [str(self.server.pk)], TaskRun.Source.MANUAL)

        self.assertEqual(TaskRun.objects.count(), 1)

    def test_target_snapshot_does_not_change_when_asset_changes_after_enqueue(self):
        """Re-reading mutable assets would rewrite the input of an old task."""
        from net.inspections.queue import enqueue_task

        task = enqueue_task(self.profile, [self.server.pk], TaskRun.Source.MANUAL)
        self.server.name = '已改名服务器'
        self.server.save(update_fields=['name'])

        self.assertEqual(task.target_runs.get().target_snapshot['name'], '应用服务器')

    def test_profile_execution_settings_are_snapshotted_before_later_profile_edits(self):
        """Reading the live profile in a worker would change the meaning of queued work."""
        from net.inspections.queue import enqueue_task

        self.profile.target_selector = {'mode': 'filtered', 'vendor': 'Ubuntu'}
        self.profile.alert_policy_mode = 'override'
        self.profile.save(update_fields=['target_selector', 'alert_policy_mode'])
        from net.models import AlertPolicy, AlertChannel
        policy = AlertPolicy.objects.create(inspection_profile=self.profile, mode='override')
        policy.channels.add(AlertChannel.objects.create(name='queued route', channel_type='feishu'))
        task = enqueue_task(self.profile, [self.server.pk], TaskRun.Source.MANUAL)
        self.profile.timeout_seconds = 300
        self.profile.target_selector = {'mode': 'all'}
        self.profile.save(update_fields=['timeout_seconds', 'target_selector'])

        self.assertEqual(task.profile_snapshot['timeout_seconds'], 60)
        self.assertEqual(
            task.profile_snapshot['target_selector'],
            {'mode': 'filtered', 'vendor': 'Ubuntu'},
        )
        self.assertEqual(task.profile_snapshot['alert_policy_mode'], 'override')

    def test_enqueue_normalizes_identity_mappings_and_detaches_parameter_snapshot(self):
        """Keeping caller-owned structures would let post-enqueue edits alter task input."""
        from net.inspections.queue import enqueue_task

        parameters = {'window': {'minutes': 15}}
        task = enqueue_task(
            self.profile,
            [{
                'target_type': 'server',
                'target_id': self.server.pk,
                'target_snapshot': {'name': 'untrusted caller value'},
            }],
            TaskRun.Source.MANUAL,
            overrides={'parameters': parameters},
        )
        parameters['window']['minutes'] = 99

        self.assertEqual(task.parameters_snapshot, {'window': {'minutes': 15}})
        self.assertEqual(task.target_runs.get().target_snapshot['name'], '应用服务器')

    def test_enqueue_rejects_declared_target_type_outside_profile_applicability(self):
        """Ignoring declared identity types could enqueue the wrong asset family."""
        from net.inspections.queue import enqueue_task

        with self.assertRaises(ValidationError) as context:
            enqueue_task(
                self.profile,
                [{'target_type': 'monitor', 'target_id': self.server.pk}],
                TaskRun.Source.MANUAL,
            )

        self.assertIn('target_type', context.exception.message_dict)
        self.assertEqual(TaskRun.objects.count(), 0)

    def test_enqueue_rejects_non_json_identity_snapshot_before_writing_task(self):
        """Accepting an unserializable supplied snapshot would defer input failure into a write."""
        from net.inspections.queue import enqueue_task

        with self.assertRaises(ValidationError):
            enqueue_task(
                self.profile,
                [{
                    'target_type': 'server',
                    'target_id': self.server.pk,
                    'target_snapshot': {'bad': {1, 2, 3}},
                }],
                TaskRun.Source.MANUAL,
            )

        self.assertEqual(TaskRun.objects.count(), 0)

    def test_enqueue_rejects_non_finite_json_numbers_without_database_writes(self):
        """Allowing NaN or infinities would defer rejection to MySQL JSON parsing."""
        from net.inspections.queue import enqueue_task

        for invalid_number in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(invalid_number=invalid_number):
                try:
                    enqueue_task(
                        self.profile,
                        [self.server.pk],
                        TaskRun.Source.MANUAL,
                        overrides={
                            'parameters': {'threshold': invalid_number},
                        },
                    )
                except ValidationError as exc:
                    self.assertIn('parameters', exc.message_dict)
                except Exception as exc:
                    self.fail(f'非有限数泄漏为数据库异常: {type(exc).__name__}')
                else:
                    self.fail('非有限数被错误写入任务快照。')

        self.assertEqual(TaskRun.objects.count(), 0)

    def test_enqueue_rejects_malformed_overrides_and_naive_availability_before_writing_task(self):
        """Passing malformed queue controls must not turn into a partial task write."""
        from net.inspections.queue import enqueue_task

        for overrides in (
            ['selected_items'],
            {'available_at': datetime(2026, 1, 1, 9, 0)},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    enqueue_task(
                        self.profile,
                        [self.server.pk],
                        TaskRun.Source.MANUAL,
                        overrides=overrides,
                    )

        self.assertEqual(TaskRun.objects.count(), 0)

    def test_enqueue_rejects_an_unrecognized_profile_without_touching_the_database(self):
        """Assuming every object is an analysis profile would turn a caller error into a 500."""
        from net.inspections.queue import enqueue_task

        with self.assertRaises(ValidationError):
            enqueue_task(object(), [self.server.pk], TaskRun.Source.MANUAL)

        self.assertEqual(TaskRun.objects.count(), 0)


class ComputerAnalysisEnqueueTests(TestCase):
    def test_enqueue_analysis_uses_only_log_targets_and_excludes_raw_payload(self):
        """Treating a computer asset as an analysis target would lose the uploaded-log audit link."""
        from net.inspections.queue import enqueue_task

        profile = ComputerAnalysisProfile.objects.create(
            name='计算机日志分析',
            analysis_items=['patches', 'defender'],
            software_policy_path='config/software-policy.ini',
            minimum_windows_release='24H2',
            defender_update_max_days=3,
            defender_scan_max_days=5,
            patch_max_days=14,
            uptime_max_hours=72,
            cpu_max_percent=85,
            memory_max_percent=80,
            kms_servers=['kms1.example.test', '192.0.2.10'],
        )
        log = create_log_file(
            source_path='C:/inspection-logs/PC-01.json',
            modified_at=timezone.now(),
            content_hash='a' * 64,
            import_status='imported',
            payload={'host': 'PC-01', 'secret_like_value': 'never snapshot raw payload'},
        )

        task = enqueue_task(profile, [log.pk], TaskRun.Source.MANUAL)

        target = task.target_runs.get()
        self.assertEqual(task.task_type, TaskRun.TaskType.COMPUTER_ANALYSIS)
        self.assertEqual(target.target_type, 'computer_log')
        self.assertEqual(target.target_id, str(log.pk))
        self.assertEqual(target.target_snapshot['content_hash'], 'a' * 64)
        self.assertNotIn('payload', target.target_snapshot)
        self.assertEqual(task.profile_snapshot['minimum_windows_release'], '24H2')
        self.assertEqual(task.profile_snapshot['patch_max_days'], 14)
        self.assertEqual(task.profile_snapshot['kms_servers'], [
            'kms1.example.test', '192.0.2.10',
        ])


class LeaseQueueTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(
            name='服务器租约巡检',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['reachability'],
        )
        self.first_server = Server.objects.create(
            name='最早服务器', ip='192.0.2.42', server_type='linux',
        )
        self.second_server = Server.objects.create(
            name='较晚服务器', ip='192.0.2.43', server_type='linux',
        )
        self.now = timezone.now().replace(microsecond=0)

    def _queue_api(self):
        try:
            from net.inspections.queue import (
                claim_next_task,
                enqueue_task,
                finish_task,
                recover_expired_tasks,
                renew_lease,
            )
        except ImportError as exc:
            self.fail(f'队列租约接口尚未提供: {exc}')
        return (
            enqueue_task,
            claim_next_task,
            renew_lease,
            recover_expired_tasks,
            finish_task,
        )

    def test_claims_oldest_available_task_once(self):
        """Removing the row-locked state transition would let two workers own one task."""
        enqueue_task, claim_next_task, _renew, _recover, _finish = self._queue_api()
        first = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now - timedelta(minutes=2)},
        )
        second = enqueue_task(
            self.profile,
            [self.second_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now - timedelta(minutes=1)},
        )

        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)

        self.assertEqual(claimed.pk, first.pk)
        self.assertEqual(claimed.status, TaskRun.Status.RUNNING)
        self.assertEqual(claimed.worker_id, 'worker-a')
        self.assertEqual(claimed.attempt_count, 1)
        self.assertEqual(claimed.lease_expires_at, self.now + timedelta(seconds=30))
        claimed_second = claim_next_task('worker-b', lease_seconds=30, now=self.now)
        self.assertEqual(claimed_second.pk, second.pk)
        self.assertIsNone(claim_next_task('worker-c', lease_seconds=30, now=self.now))

    def test_cancel_task_atomically_finishes_every_unfinished_target(self):
        """Leaving queued targets active would let a Worker continue a cancelled task."""
        from net.inspections.queue import cancel_task, claim_next_task, enqueue_task, renew_lease

        task = enqueue_task(
            self.profile,
            [self.first_server.pk, self.second_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        self.assertEqual(claimed.status, TaskRun.Status.RUNNING)

        cancelled = cancel_task(task.pk, now=self.now)

        self.assertEqual(cancelled.status, TaskRun.Status.CANCELLED)
        self.assertEqual(cancelled.progress, 100)
        self.assertEqual(cancelled.completed_targets, 2)
        self.assertEqual(cancelled.successful_targets, 0)
        self.assertEqual(cancelled.failed_targets, 0)
        self.assertEqual(cancelled.finished_at, self.now)
        self.assertIsNone(cancelled.lease_expires_at)
        self.assertIsNone(cancelled.active_scope_key)
        self.assertFalse(renew_lease(cancelled.pk, 'worker-a', lease_seconds=30))
        self.assertEqual(
            set(cancelled.target_runs.values_list('status', flat=True)),
            {TaskRun.Status.CANCELLED},
        )
        self.assertTrue(all(
            value == self.now
            for value in cancelled.target_runs.values_list('finished_at', flat=True)
        ))

    def test_cancel_task_rejects_an_already_finished_task_without_changes(self):
        """A repeated stop must not rewrite immutable terminal task history."""
        from net.inspections.queue import cancel_task, enqueue_task

        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        first_cancel = cancel_task(task.pk, now=self.now)

        with self.assertRaisesMessage(ValidationError, '任务已经结束'):
            cancel_task(task.pk, now=self.now + timedelta(seconds=1))

        task.refresh_from_db()
        self.assertEqual(task.finished_at, first_cancel.finished_at)

    def test_renewal_requires_current_lease_owner(self):
        """Ignoring ownership would let another worker extend a stolen lease."""
        enqueue_task, claim_next_task, renew_lease, _recover, _finish = self._queue_api()
        enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)

        self.assertFalse(renew_lease(claimed.pk, 'worker-b', lease_seconds=30))
        claimed.refresh_from_db()
        before_renewal = claimed.lease_expires_at
        self.assertTrue(renew_lease(claimed.pk, 'worker-a', lease_seconds=30))
        claimed.refresh_from_db()
        self.assertGreater(claimed.lease_expires_at, before_renewal)

    def test_repeated_early_renewals_keep_expiry_within_one_lease_window(self):
        """Extending from the prior deadline would accumulate hours of stale lease time."""
        enqueue_task, claim_next_task, renew_lease, _recover, _finish = self._queue_api()
        enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=60, now=self.now)

        for elapsed_seconds in (10, 20, 30):
            heartbeat_at = self.now + timedelta(seconds=elapsed_seconds)
            with patch('net.inspections.queue.timezone.now', return_value=heartbeat_at):
                self.assertTrue(
                    renew_lease(claimed.pk, 'worker-a', lease_seconds=60),
                )

        claimed.refresh_from_db()
        self.assertEqual(
            claimed.lease_expires_at,
            self.now + timedelta(seconds=90),
        )

    def test_expired_running_task_is_requeued_for_one_later_owner(self):
        """Leaving expired leases running would permanently strand queued work."""
        enqueue_task, claim_next_task, _renew, recover_expired_tasks, _finish = self._queue_api()
        enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        recovery_time = self.now + timedelta(seconds=31)

        self.assertEqual(recover_expired_tasks(now=recovery_time), 1)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, TaskRun.Status.QUEUED)
        self.assertEqual(claimed.worker_id, '')
        self.assertIsNone(claimed.lease_expires_at)
        reclaimed = claim_next_task('worker-b', lease_seconds=30, now=recovery_time)
        self.assertEqual(reclaimed.pk, claimed.pk)
        self.assertEqual(reclaimed.worker_id, 'worker-b')
        self.assertEqual(reclaimed.attempt_count, 2)

    def test_recovery_and_finish_use_immutable_target_type_after_profile_change(self):
        """Reading the live profile would invalidate a queued server task after an edit."""
        enqueue_task, claim_next_task, _renew, recover_expired_tasks, finish_task = (
            self._queue_api()
        )
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        target = claimed.target_runs.get()
        target.status = TaskRun.Status.RUNNING
        target.started_at = self.now
        target.save()
        self.profile.device_type = InspectionProfile.DeviceType.NETWORK_DEVICE
        self.profile.save(update_fields=['device_type'])
        recovery_at = self.now + timedelta(seconds=31)

        try:
            self.assertEqual(recover_expired_tasks(now=recovery_at), 1)
        except ValidationError as exc:
            self.fail(f'恢复读取了可变的实时配置: {exc}')

        reclaimed = claim_next_task(
            'worker-b', lease_seconds=30, now=recovery_at,
        )
        target.refresh_from_db()
        target.status = TaskRun.Status.SUCCESS
        target.started_at = recovery_at
        target.finished_at = recovery_at
        target.save()
        with patch(
            'net.inspections.queue.timezone.now',
            return_value=recovery_at + timedelta(seconds=1),
        ):
            finished = finish_task(task.pk, 'worker-b')

        self.assertEqual(finished.status, TaskRun.Status.SUCCESS)

    def test_finish_aggregates_target_outcomes_and_preserves_full_error(self):
        """Skipping aggregation would leave the task active and hide failure context."""
        enqueue_task, claim_next_task, _renew, _recover, finish_task = self._queue_api()
        task = enqueue_task(
            self.profile,
            [self.first_server.pk, self.second_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        targets = list(claimed.target_runs.order_by('target_id'))
        targets[0].status = TaskRun.Status.SUCCESS
        targets[0].started_at = self.now
        targets[0].finished_at = self.now
        targets[0].save()
        full_error = 'connection refused: ' + ('x' * 3000)
        targets[1].status = TaskRun.Status.FAILED
        targets[1].started_at = self.now
        targets[1].finished_at = self.now
        targets[1].error_message = full_error
        targets[1].save()

        finished = finish_task(task.pk, 'worker-a')

        self.assertEqual(finished.status, TaskRun.Status.PARTIAL)
        self.assertEqual(finished.progress, 100)
        self.assertEqual(finished.completed_targets, 2)
        self.assertEqual(finished.successful_targets, 1)
        self.assertEqual(finished.failed_targets, 1)
        self.assertIsNone(finished.active_scope_key)
        self.assertLess(len(finished.error_summary), len(full_error))
        self.assertEqual(
            finished.target_runs.get(pk=targets[1].pk).error_message,
            full_error,
        )

    def test_finish_keeps_one_partial_target_as_parent_partial_with_zero_successes(self):
        enqueue_task, claim_next_task, _renew, _recover, finish_task = self._queue_api()
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        target = claimed.target_runs.get()
        target.status = TaskRun.Status.PARTIAL
        target.started_at = self.now
        target.finished_at = self.now
        target.save()

        finished = finish_task(task.pk, 'worker-a')

        self.assertEqual(finished.status, TaskRun.Status.PARTIAL)
        self.assertEqual(finished.successful_targets, 0)
        self.assertEqual(finished.failed_targets, 1)

    def test_finish_rejects_target_terminal_state_that_bypassed_its_audit_contract(self):
        """Trusting raw target status alone would aggregate a record without a finish time."""
        enqueue_task, claim_next_task, _renew, _recover, finish_task = self._queue_api()
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claim_next_task('worker-a', lease_seconds=30, now=self.now)
        task.target_runs.update(status=TaskRun.Status.FAILED)

        with self.assertRaises(ValidationError):
            finish_task(task.pk, 'worker-a')

        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.RUNNING)

    def test_stale_worker_cannot_finish_or_release_a_reclaimed_task(self):
        """Omitting completion ownership would let stale A terminate B's live task."""
        enqueue_task, claim_next_task, _renew, recover_expired_tasks, finish_task = (
            self._queue_api()
        )
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claim_next_task('worker-a', lease_seconds=30, now=self.now)
        reclaimed_at = self.now + timedelta(seconds=31)
        self.assertEqual(recover_expired_tasks(now=reclaimed_at), 1)
        reclaimed = claim_next_task(
            'worker-b', lease_seconds=30, now=reclaimed_at,
        )
        target = reclaimed.target_runs.get()
        target.status = TaskRun.Status.SUCCESS
        target.started_at = reclaimed_at
        target.finished_at = reclaimed_at
        target.save()
        worker_b_lease = reclaimed.lease_expires_at

        try:
            with patch(
                'net.inspections.queue.timezone.now',
                return_value=reclaimed_at + timedelta(seconds=1),
            ):
                with self.assertRaises(ValidationError):
                    finish_task(task.pk, 'worker-a')
        except TypeError as exc:
            self.fail(f'finish_task 尚未接收 Worker 所有权: {exc}')

        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.RUNNING)
        self.assertEqual(task.worker_id, 'worker-b')
        self.assertEqual(task.lease_expires_at, worker_b_lease)
        self.assertEqual(task.active_scope_key, task.scope_key)
        with patch(
            'net.inspections.queue.timezone.now',
            return_value=reclaimed_at + timedelta(seconds=2),
        ):
            finished = finish_task(task.pk, 'worker-b')
        self.assertEqual(finished.status, TaskRun.Status.SUCCESS)

    def test_current_worker_cannot_finish_after_its_lease_expires(self):
        """Checking only worker identity would accept completion after ownership expired."""
        enqueue_task, claim_next_task, _renew, _recover, finish_task = self._queue_api()
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        target = claimed.target_runs.get()
        target.status = TaskRun.Status.SUCCESS
        target.started_at = self.now
        target.finished_at = self.now
        target.save()
        original_lease = claimed.lease_expires_at

        with patch(
            'net.inspections.queue.timezone.now',
            return_value=self.now + timedelta(seconds=31),
        ):
            with self.assertRaises(ValidationError):
                finish_task(task.pk, 'worker-a')

        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.RUNNING)
        self.assertEqual(task.lease_expires_at, original_lease)
        self.assertEqual(task.active_scope_key, task.scope_key)

    def test_recovery_marks_task_failed_after_bounded_lease_attempts(self):
        """Removing the retry ceiling would leave repeatedly abandoned tasks active forever."""
        enqueue_task, claim_next_task, _renew, recover_expired_tasks, _finish = self._queue_api()
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        attempt_now = self.now
        for attempt in range(3):
            claimed = claim_next_task(
                f'worker-{attempt}', lease_seconds=30, now=attempt_now,
            )
            self.assertEqual(claimed.pk, task.pk)
            attempt_now += timedelta(seconds=31)
            self.assertEqual(recover_expired_tasks(now=attempt_now), 1)

        task.refresh_from_db()
        target = task.target_runs.get()
        self.assertEqual(task.status, TaskRun.Status.FAILED)
        self.assertEqual(task.attempt_count, 3)
        self.assertIsNone(task.active_scope_key)
        self.assertEqual(target.status, TaskRun.Status.FAILED)
        self.assertIn('最大重试次数', target.error_message)

    def test_expired_task_with_all_successful_targets_aggregates_without_retry(self):
        """Requeueing completed targets would consume an attempt and block duplicate scope."""
        enqueue_task, claim_next_task, _renew, recover_expired_tasks, _finish = (
            self._queue_api()
        )
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        target = claimed.target_runs.get()
        target.status = TaskRun.Status.SUCCESS
        target.started_at = self.now
        target.finished_at = self.now
        target.save()

        self.assertEqual(
            recover_expired_tasks(now=self.now + timedelta(seconds=31)),
            1,
        )

        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.SUCCESS)
        self.assertEqual(task.attempt_count, 1)
        self.assertIsNone(task.active_scope_key)

    def test_expired_task_with_mixed_terminal_targets_aggregates_without_retry(self):
        """Requeueing mixed terminal targets would duplicate already recorded execution."""
        enqueue_task, claim_next_task, _renew, recover_expired_tasks, _finish = (
            self._queue_api()
        )
        task = enqueue_task(
            self.profile,
            [self.first_server.pk, self.second_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        targets = list(claimed.target_runs.order_by('target_id'))
        for target, status in zip(
            targets,
            (TaskRun.Status.SUCCESS, TaskRun.Status.FAILED),
        ):
            target.status = status
            target.started_at = self.now
            target.finished_at = self.now
            target.save()

        self.assertEqual(
            recover_expired_tasks(now=self.now + timedelta(seconds=31)),
            1,
        )

        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.PARTIAL)
        self.assertEqual(task.attempt_count, 1)
        self.assertEqual(task.successful_targets, 1)
        self.assertEqual(task.failed_targets, 1)
        self.assertIsNone(task.active_scope_key)

    def test_finish_rejects_queued_or_incomplete_task_without_releasing_scope(self):
        """Finalizing before all targets end would release the duplicate guard too early."""
        enqueue_task, _claim, _renew, _recover, finish_task = self._queue_api()
        queued = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )

        with self.assertRaises(ValidationError):
            finish_task(queued.pk, 'worker-a')

        queued.refresh_from_db()
        self.assertEqual(queued.status, TaskRun.Status.QUEUED)
        self.assertEqual(queued.active_scope_key, queued.scope_key)

    def test_expired_lease_cannot_be_renewed_by_its_old_owner(self):
        """Renewing after expiry would let a worker reclaim work without recovery."""
        enqueue_task, claim_next_task, renew_lease, _recover, _finish = self._queue_api()
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now - timedelta(minutes=2)},
        )
        claim_next_task('worker-a', lease_seconds=30, now=self.now - timedelta(minutes=1))

        self.assertFalse(renew_lease(task.pk, 'worker-a', lease_seconds=30))

    def test_renew_lease_returns_false_for_an_unknown_or_invalid_task_identity(self):
        """Letting malformed heartbeat IDs escape would crash a healthy worker loop."""
        _enqueue, _claim, renew_lease, _recover, _finish = self._queue_api()

        self.assertFalse(renew_lease('not-a-uuid', 'worker-a', lease_seconds=30))

    def test_terminal_completion_releases_scope_for_a_later_run(self):
        """Keeping the active slot after completion would block the next legitimate inspection."""
        enqueue_task, claim_next_task, _renew, _recover, finish_task = self._queue_api()
        task = enqueue_task(
            self.profile,
            [self.first_server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': self.now},
        )
        claimed = claim_next_task('worker-a', lease_seconds=30, now=self.now)
        target = claimed.target_runs.get()
        target.status = TaskRun.Status.SUCCESS
        target.started_at = self.now
        target.finished_at = self.now
        target.save()
        finish_task(task.pk, 'worker-a')

        later = enqueue_task(self.profile, [self.first_server.pk], TaskRun.Source.MANUAL)
        self.assertNotEqual(later.pk, task.pk)


class QueueSerializationTests(TransactionTestCase):
    def test_serialized_claimers_never_receive_the_same_task(self):
        """Replacing the locked claim transition would duplicate a worker's ownership."""
        from net.inspections.queue import claim_next_task, enqueue_task

        profile = InspectionProfile.objects.create(
            name='串行抢占巡检',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['reachability'],
        )
        server = Server.objects.create(
            name='串行服务器', ip='192.0.2.44', server_type='linux',
        )
        now = timezone.now()
        queued = enqueue_task(
            profile,
            [server.pk],
            TaskRun.Source.MANUAL,
            overrides={'available_at': now},
        )

        first = claim_next_task('worker-one', lease_seconds=30, now=now)
        second = claim_next_task('worker-two', lease_seconds=30, now=now)

        self.assertEqual(first.pk, queued.pk)
        self.assertIsNone(second)
        queued.refresh_from_db()
        self.assertEqual(queued.worker_id, 'worker-one')
