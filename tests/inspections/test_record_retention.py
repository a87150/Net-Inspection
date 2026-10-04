"""Retention bounds the three infrastructure inspection tables.

These are the only tables that grow with (devices x runs), and the asset list
derives each device's status from the newest row, so the rules under test are:
expire past the window, never touch the newest row per asset, and never touch a
row whose alerts are still being processed.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from net.inspections.retention import cleanup_inspection_records
from net.models import (Error_Network_Device, InspectionProfile, Network_Device,
                        Network_Device_Inspection, TaskRun, TaskTargetRun)


class InspectionRetentionTests(TestCase):
    def setUp(self):
        self.device = Network_Device.objects.create(device_name='edge', ip='192.0.2.10')
        self.profile = InspectionProfile.objects.create(
            name='retention', device_type='network_device',
            selected_items=['device_info'], record_retention='30',
        )

    def record(self, *, retention='30', age_days=0, alert_complete=True, device=None):
        device = device or self.device
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION, source=TaskRun.Source.MANUAL,
            status=TaskRun.Status.SUCCESS, progress=100, finished_at=timezone.now(),
            inspection_profile=self.profile,
            profile_snapshot={'record_retention': retention},
            alert_summary_processed_at=timezone.now() if alert_complete else None,
        )
        target = TaskTargetRun.objects.create(
            task=task, target_type='network_device', target_id=str(device.pk),
            alert_processed_at=timezone.now() if alert_complete else None,
        )
        inspection = Network_Device_Inspection.objects.create(
            device=device, task_target=target, summary='row',
        )
        if age_days:
            # created_at is auto_now_add, so backdate it explicitly.
            Network_Device_Inspection.objects.filter(pk=inspection.pk).update(
                created_at=timezone.now() - timedelta(days=age_days))
            inspection.refresh_from_db()
        return inspection

    def test_history_past_the_window_is_removed_and_recent_rows_kept(self):
        old = self.record(age_days=40)
        fresh = self.record(age_days=1)
        cleanup_inspection_records()
        self.assertFalse(Network_Device_Inspection.objects.filter(pk=old.pk).exists())
        self.assertTrue(Network_Device_Inspection.objects.filter(pk=fresh.pk).exists())

    def test_newest_row_per_asset_survives_even_when_past_the_window(self):
        # Otherwise the asset list would show every device as unchecked.
        newest = self.record(age_days=400)
        older = self.record(age_days=500)
        cleanup_inspection_records()
        self.assertTrue(Network_Device_Inspection.objects.filter(pk=newest.pk).exists())
        self.assertFalse(Network_Device_Inspection.objects.filter(pk=older.pk).exists())

    def test_pending_alert_processing_blocks_removal(self):
        pending = self.record(age_days=400, alert_complete=False)
        cleanup_inspection_records()
        self.assertTrue(Network_Device_Inspection.objects.filter(pk=pending.pk).exists())

    def test_none_keeps_only_the_newest_row_per_asset(self):
        self.record(retention='none', age_days=3)
        latest = self.record(retention='none', age_days=2)
        cleanup_inspection_records()
        remaining = list(Network_Device_Inspection.objects.values_list('pk', flat=True))
        self.assertEqual(remaining, [latest.pk])

    def test_each_task_retains_by_its_own_snapshot(self):
        other = Network_Device.objects.create(device_name='core', ip='192.0.2.11')
        long_lived = self.record(retention='180', age_days=40)
        short_lived = self.record(retention='30', age_days=40, device=other)
        # A newer row on the same device, so short_lived is not protected by the
        # "newest per asset" rule and this test really exercises the snapshot value.
        self.record(retention='30', age_days=1, device=other)
        cleanup_inspection_records()
        self.assertTrue(Network_Device_Inspection.objects.filter(pk=long_lived.pk).exists())
        self.assertFalse(Network_Device_Inspection.objects.filter(pk=short_lived.pk).exists())

    def test_legacy_task_snapshot_falls_back_to_the_profile_setting(self):
        # A task created before record_retention existed carries no key in its
        # snapshot. Without the fallback its rows would be unreachable by retention
        # and grow forever.
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION, source=TaskRun.Source.MANUAL,
            status=TaskRun.Status.SUCCESS, progress=100, finished_at=timezone.now(),
            inspection_profile=self.profile, profile_snapshot={},
            alert_summary_processed_at=timezone.now(),
        )
        target = TaskTargetRun.objects.create(
            task=task, target_type='network_device', target_id=str(self.device.pk),
            alert_processed_at=timezone.now(),
        )
        legacy = Network_Device_Inspection.objects.create(
            device=self.device, task_target=target, summary='legacy',
        )
        Network_Device_Inspection.objects.filter(pk=legacy.pk).update(
            created_at=timezone.now() - timedelta(days=900))
        self.record(age_days=1)  # a newer row, so legacy is not protected as newest
        cleanup_inspection_records()
        self.assertFalse(
            Network_Device_Inspection.objects.filter(pk=legacy.pk).exists(),
            'legacy row survived retention',
        )

    def test_error_rows_follow_their_inspection(self):
        stale = self.record(age_days=40)
        self.record(age_days=1)
        error = Error_Network_Device.objects.create(
            inspection=stale, error_message={'message': 'boom'},
        )
        cleanup_inspection_records()
        self.assertFalse(Error_Network_Device.objects.filter(pk=error.pk).exists())
