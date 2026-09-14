from unittest.mock import patch
from django.test import TestCase
from net.models import InspectionProfile, Server
from net.inspections.queue import enqueue_task, claim_next_task
from net.inspections.executor import _asset_context, execute_target


class ConnectionEditGuardTests(TestCase):
    def setUp(self):
        self.server = Server.objects.create(ip='192.0.2.99', server_type='linux',
                                            username='old-user', password='old-password')
        self.profile = InspectionProfile.objects.create(name='single', device_type='server',
                                                         selected_items=['cpu'])
        self.task = enqueue_task(self.profile, [self.server.pk], 'manual')
        self.target = self.task.target_runs.get()

    def test_changed_endpoint_does_not_receive_new_credentials(self):
        self.server.ip = '192.0.2.100'
        self.server.username = 'new-user'
        self.server.password = 'new-password'
        self.server.save()
        claim_next_task('edit-guard', 60)
        with patch('net.inspections.executor._collect', side_effect=AssertionError('must not connect')) as collect:
            outcome = execute_target(self.target, worker_id='edit-guard')
        collect.assert_not_called()
        self.assertEqual(outcome.status, 'failed')
        self.target.refresh_from_db()
        self.assertIn('连接配置', self.target.error_message)
        self.assertNotIn('new-password', self.target.error_message)

    def test_name_edit_keeps_frozen_name_and_current_credentials(self):
        original = self.target.target_snapshot['name']
        self.server.name = 'renamed'
        self.server.password = 'rotated'
        self.server.save()
        asset = _asset_context(self.target)
        self.assertEqual(asset.name, original)
        self.assertEqual(asset.password, 'rotated')
