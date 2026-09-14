from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from net.infrastructure.collection import CollectionResult
from net.inspections.executor import execute_target, _collect
from net.inspections.queue import enqueue_task, claim_next_task
from net.models import InspectionProfile, Network_Device, Network_Device_Inspection, TaskRun


@override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode())
class DailyConfigurationBackupTests(TestCase):
    def setUp(self):
        self.asset = Network_Device.objects.create(device_name='switch', ip='192.0.2.10',
                                                   vendor='cisco', username='reader', password='secret')
        self.profile = InspectionProfile.objects.create(name='daily', device_type='network_device',
                                                        selected_items=['cpu'])

    def claimed(self):
        enqueue_task(self.profile, [self.asset.pk], TaskRun.Source.MANUAL)
        task = claim_next_task('backup-test', 60)
        return task.target_runs.get(), task

    def test_configuration_is_automatically_part_of_network_inspection(self):
        target, task = self.claimed()
        self.assertEqual(task.selected_items_snapshot, ['cpu', 'config_info'])

    @patch('net.inspections.executor.collect_network')
    def test_raw_configuration_is_backed_up_but_not_saved_in_inspection(self, collect):
        from net.devices.configuration_backups import latest_configuration_backup, read_configuration_backup
        body = 'hostname switch\r\nusername admin secret original-secret\r\nend\r\n'
        item = dict(status='success', vendor='cisco', format='text', complete=True,
                    scope='running-config', content=body)
        collect.return_value = CollectionResult(True, 'success',
            data={'cpu': {'usage_percent': 10}, 'config_info': item}, raw={'config_info': item})
        target, task = self.claimed()
        self.assertEqual(execute_target(target, worker_id='backup-test').status, 'success')
        backup = latest_configuration_backup(self.asset)
        self.assertEqual(read_configuration_backup(backup), body.encode())
        record = Network_Device_Inspection.objects.get()
        self.assertNotIn('original-secret', str(record.details) + str(record.raw_output))
        self.assertEqual(record.details['config_info']['backup_id'], str(backup.pk))
        collect.reset_mock()
        collect.return_value = CollectionResult(True, 'success', data={'cpu': {'usage_percent': 12}})
        result = _collect(target, task, self.asset)
        self.assertEqual(collect.call_args.kwargs['selected_items'], ['cpu'])
        self.assertEqual(result.data['config_info']['backup_id'], str(backup.pk))
        task.selected_items_snapshot = ['config_info']
        collect.reset_mock()
        collect.return_value = CollectionResult(True, 'success', data={'device_info': {'name': 'switch'}})
        reused = _collect(target, task, self.asset)
        self.assertEqual(collect.call_args.kwargs['selected_items'], ['device_info'])
        self.assertTrue(reused.reachable)

    def test_snmp_mode_routes_only_configuration_to_ssh(self):
        from net.devices.network.collector import _network_item_plan
        self.assertEqual(_network_item_plan('snmp', ['cpu', 'config_info']),
                         (['cpu'], ['config_info'], False))

    def test_unsupported_backup_is_a_configuration_issue_not_connection_failure(self):
        from net.inspections.device_issues import evaluate_device_issues
        findings, normal = evaluate_device_issues('networks', ['config_info'],
            {'config_info': {'status': 'unsupported'}}, reachable=True, status='partial')
        self.assertEqual([issue['analysis_item'] for issue in findings], ['config_info'])
        self.assertIn('inspection_collection', normal)

    @patch('net.inspections.executor.collect_network')
    def test_key_failure_keeps_retry_due_and_does_not_leak_raw(self, collect):
        from net.devices.configuration_backups import backup_due
        from net.models import DeviceConfigurationBackup
        item = dict(status='success', vendor='cisco', format='text', complete=True,
                    scope='running-config', content='hostname edge\npassword raw-secret\nend\n')
        collect.return_value = CollectionResult(True, 'success',
            data={'cpu': {'usage_percent': 10}, 'config_info': item}, raw={'config_info': item})
        target, task = self.claimed()
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=''):
            outcome = execute_target(target, worker_id='backup-test')
        self.assertEqual(outcome.status, 'partial')
        self.assertFalse(DeviceConfigurationBackup.objects.exists())
        self.assertTrue(backup_due(self.asset))
        record = Network_Device_Inspection.objects.get()
        self.assertNotIn('raw-secret', str(record.details) + str(record.raw_output))
        self.assertEqual(record.details['config_info']['status'], 'failed')

    @patch('net.inspections.executor.collect_network')
    def test_lease_loss_rolls_back_backup_with_inspection(self, collect):
        import threading
        from django.db.models.signals import post_save
        from net.models import DeviceConfigurationBackup
        guard = threading.Event()
        target, task = self.claimed()
        collect.return_value = CollectionResult(True, 'success', data={'config_info': dict(
            status='success', vendor='cisco', format='text', complete=True,
            scope='running-config', content='hostname edge\nend\n')})
        def lose_lease(**kwargs):
            guard.set()
        post_save.connect(lose_lease, sender=Network_Device_Inspection)
        try:
            outcome = execute_target(target, worker_id='backup-test', lease_guard=guard)
        finally:
            post_save.disconnect(lose_lease, sender=Network_Device_Inspection)
        self.assertTrue(outcome.stale)
        self.assertFalse(DeviceConfigurationBackup.objects.exists())
        self.assertFalse(Network_Device_Inspection.objects.exists())
