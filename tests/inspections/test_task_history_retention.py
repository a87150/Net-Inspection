"""Task-history retention: a task is only removed when nothing still points at it."""
from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from net.inspections.retention import cleanup_task_history
from net.models import (InspectionProfile, Network_Device, Network_Device_Inspection,
                        TaskRun, TaskTargetRun, TopologyDiscoveryBatch)


class TaskHistoryRetentionTests(TestCase):
    def setUp(self):
        self.device = Network_Device.objects.create(device_name='edge', ip='192.0.2.10')
        self.profile = InspectionProfile.objects.create(
            name='retention', device_type='network_device',
            selected_items=['device_info'], record_retention='180',
        )

    def task(self, *, age_days=0, status=TaskRun.Status.SUCCESS, with_target=True):
        """A constraint-valid task; net_task_state_shape_ck pins the field shapes."""
        finished = timezone.now() - timedelta(days=age_days)
        state = {'status': status}
        if status == TaskRun.Status.SUCCESS:
            state.update(finished_at=finished, progress=100,
                         total_targets=1, successful_targets=1,
                         completed_targets=1, failed_targets=0)
        elif status == TaskRun.Status.RUNNING:
            state.update(started_at=finished, progress=35, worker_id='retention-test',
                         lease_expires_at=timezone.now() + timedelta(minutes=5))
        elif status == TaskRun.Status.QUEUED:
            state.update(progress=0)
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION, source=TaskRun.Source.MANUAL,
            inspection_profile=self.profile, profile_snapshot={'record_retention': '180'},
            alert_summary_processed_at=timezone.now(),
            # save() derives scope_key (and the UNIQUE active_scope_key) from this,
            # so two active tasks need distinct snapshots to coexist.
            target_scope_snapshot={'targets': [
                {'target_type': 'network_device', 'target_id': str(uuid4())},
            ]}, **state)
        target = None
        if with_target:
            target = TaskTargetRun.objects.create(
                task=task, target_type='network_device', target_id=str(self.device.pk),
                alert_processed_at=timezone.now(),
            )
        return task, target

    def test_finished_tasks_past_the_window_are_removed_with_their_targets(self):
        old, _ = self.task(age_days=40)
        fresh, _ = self.task(age_days=1)
        removed = cleanup_task_history(30)
        self.assertEqual(removed, 1)
        self.assertFalse(TaskRun.objects.filter(pk=old.pk).exists())
        self.assertFalse(TaskTargetRun.objects.filter(task_id=old.pk).exists())
        self.assertTrue(TaskRun.objects.filter(pk=fresh.pk).exists())

    def test_running_and_queued_tasks_are_never_removed(self):
        running, _ = self.task(status=TaskRun.Status.RUNNING)
        queued, _ = self.task(status=TaskRun.Status.QUEUED)
        cleanup_task_history(30)
        self.assertTrue(TaskRun.objects.filter(pk=running.pk).exists())
        self.assertTrue(TaskRun.objects.filter(pk=queued.pk).exists())

    def test_task_held_by_a_record_is_kept(self):
        # Record retention has not expired this one, so its target run is still
        # PROTECTed and the whole task must stay.
        task, target = self.task(age_days=400)
        Network_Device_Inspection.objects.create(
            device=self.device, task_target=target, summary='kept',
        )
        removed = cleanup_task_history(30)
        self.assertEqual(removed, 0)
        self.assertTrue(TaskRun.objects.filter(pk=task.pk).exists())

    def test_task_held_by_a_topology_batch_is_kept(self):
        task, target = self.task(age_days=400)
        TopologyDiscoveryBatch.objects.create(
            device=self.device, source_task=task, source_target=target,
            protocol=TopologyDiscoveryBatch.Protocol.SNMP_LLDP,
            status=TopologyDiscoveryBatch.Status.SUCCESS,
            started_at=timezone.now() - timedelta(days=400),
        )
        removed = cleanup_task_history(30)
        self.assertEqual(removed, 0)
        self.assertTrue(TaskRun.objects.filter(pk=task.pk).exists())

    def test_a_blocked_task_does_not_stop_later_tasks_from_being_removed(self):
        blocked, blocked_target = self.task(age_days=500)
        TopologyDiscoveryBatch.objects.create(
            device=self.device, source_task=blocked, source_target=blocked_target,
            protocol=TopologyDiscoveryBatch.Protocol.SNMP_LLDP,
            status=TopologyDiscoveryBatch.Status.SUCCESS,
            started_at=timezone.now() - timedelta(days=500),
        )
        removable, _ = self.task(age_days=400)
        removed = cleanup_task_history(30)
        self.assertEqual(removed, 1)
        self.assertTrue(TaskRun.objects.filter(pk=blocked.pk).exists())
        self.assertFalse(TaskRun.objects.filter(pk=removable.pk).exists())
