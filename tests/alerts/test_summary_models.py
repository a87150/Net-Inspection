from importlib import import_module

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.test import TestCase
from django.utils import timezone

from net import models as contracts
from net.models import AlertEvent, InspectionProfile, TaskRun, TaskTargetRun


class SummaryModelTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(name='Summary', device_type='server')
        self.task = TaskRun.objects.create(
            task_type='inspection', inspection_profile=self.profile,
            scope_key='summary', active_scope_key='summary',
        )

    def summary(self, **kwargs):
        self.assertIn('summary_task', {f.name for f in AlertEvent._meta.fields})
        values = dict(task=self.task, summary_task=self.task, event_type='summary',
                      summary_data={'total': 2})
        values.update(kwargs)
        return AlertEvent(**values)

    def test_summary_derives_task_scope_and_is_unique(self):
        event = self.summary()
        event.save()
        self.assertEqual((event.profile_type, event.profile_id, event.target_type, event.target_id),
                         ('inspection_profile', str(self.profile.pk), 'task', str(self.task.pk)))
        self.assertIsNone(event.target_run_id)
        self.assertEqual(self.task.summary_alert.pk, event.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.summary().save()

    def test_summary_rejects_wrong_scope_and_target(self):
        target = TaskTargetRun.objects.create(task=self.task, target_type='server', target_id='one')
        for changes in ({'summary_task': None}, {'target_run': target},
                        {'target_type': 'server'}, {'target_id': 'wrong'},
                        {'profile_id': 'wrong'}, {'event_type': 'abnormal'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.summary(**changes).save()

    def test_summary_relation_cannot_bypass_scope_guard(self):
        event = self.summary()
        event.save()
        for field in ('summary_task', 'summary_task_id'):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AlertEvent.objects.filter(pk=event.pk).update(**{field: None})

    def test_summary_bulk_create_and_get_or_create_use_canonical_scope(self):
        event = self.summary()
        AlertEvent.objects.bulk_create([event])
        existing, created = AlertEvent.objects.get_or_create(
            summary_task=self.task, defaults={'task': self.task, 'event_type': 'summary'})
        self.assertFalse(created)
        self.assertEqual(existing.pk, event.pk)
        self.assertEqual(existing.target_id, str(self.task.pk))
        with self.assertRaises(ValidationError):
            AlertEvent.objects.bulk_create([self.summary(profile_id='forged')])

    def test_summary_reuses_profile_snapshot_and_policy_validation(self):
        from net.models import AlertPolicy, ComputerAnalysisProfile

        profile = ComputerAnalysisProfile.objects.create(name='Analysis')
        task = TaskRun.objects.create(task_type='computer_analysis', analysis_profile=profile,
                                     scope_key='analysis', active_scope_key='analysis')
        event = self.summary(task=task, summary_task=task)
        event.save()
        self.assertEqual((event.profile_type, event.profile_id),
                         ('computer_analysis_profile', str(profile.pk)))
        wrong_policy = AlertPolicy.objects.create(name='other', analysis_profile=profile)
        with self.assertRaises(ValidationError):
            self.summary(policy=wrong_policy).save()
        TaskRun.objects.filter(pk=self.task.pk).update(profile_snapshot={'id': 'forged'})
        with self.assertRaises(ValidationError):
            self.summary().save()

    def test_database_rejects_invalid_summary_shape(self):
        event = self.summary()
        event.save()
        other = TaskRun.objects.create(task_type='inspection', inspection_profile=self.profile,
                                       target_scope_snapshot={'target_ids': ['other']},
                                       scope_key='other', active_scope_key='other')
        for changes in ({'summary_task_id': None}, {'event_type': 'abnormal'},
                        {'target_type': 'server'}, {'task_id': other.pk}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                models.QuerySet(model=AlertEvent).filter(pk=event.pk).update(**changes)

    def test_device_audit_can_be_recorded(self):
        self.assertIn('recorded', AlertEvent.Status.values)
        target = TaskTargetRun.objects.create(task=self.task, target_type='server', target_id='one')
        event = AlertEvent.objects.create(task=self.task, target_run=target,
                                          event_type='abnormal', status='recorded')
        self.assertIsNone(event.summary_task_id)

    def test_task_summary_processing_state_persists(self):
        self.assertIn('alert_summary_processed_at', {f.name for f in TaskRun._meta.fields})
        now = timezone.now()
        TaskRun.objects.filter(pk=self.task.pk).update(
            alert_summary_processed_at=now, alert_summary_attempted_at=now, alert_summary_error='error')
        self.task.refresh_from_db()
        self.assertEqual(self.task.alert_summary_processed_at, now)
        self.assertEqual(self.task.alert_summary_attempted_at, now)
        self.assertEqual(self.task.alert_summary_error, 'error')

    def test_template_singleton_and_mode_are_enforced(self):
        template_model = getattr(contracts, 'AlertNotificationTemplate', None)
        self.assertIsNotNone(template_model)
        template = template_model.objects.create()
        self.assertEqual((template.pk, template.mode), ('task_summary', 'compact'))
        template.title_template = '{{ arbitrary_template_module_syntax }}'
        template.save()
        for values in ({'key': 'other'}, {'mode': 'other'}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                template_model(**values).save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            template_model.objects.filter(pk=template.pk).update(key='other')

    def test_migration_retires_pending_delivery_and_marks_historical_tasks(self):
        from django.db import connection
        from net.models import AlertChannel, AlertDelivery

        self.assertIn('alert_summary_processed_at', {f.name for f in TaskRun._meta.fields})
        target = TaskTargetRun.objects.create(task=self.task, target_type='server', target_id='one')
        event = AlertEvent.objects.create(task=self.task, target_run=target, event_type='abnormal')
        channel = AlertChannel.objects.create(name='one', channel_type='feishu', settings={})
        delivery = AlertDelivery.objects.create(event=event, channel=channel, status='sending',
            lease_token='lease', lease_expires_at=timezone.now())
        retry_channel = AlertChannel.objects.create(name='retry', channel_type='feishu', settings={})
        pending_channel = AlertChannel.objects.create(name='pending', channel_type='feishu', settings={})
        retry = AlertDelivery.objects.create(event=event, channel=retry_channel, status='retry',
                                            next_attempt_at=timezone.now(), attempt_count=1)
        pending = AlertDelivery.objects.create(event=event, channel=pending_channel)
        sent_event = AlertEvent.objects.create(task=self.task, target_run=target,
                                               event_type='recovery', status='delivered')
        sent = AlertDelivery.objects.create(event=sent_event, channel=channel, status='sent',
                                           delivered_at=timezone.now(), response_summary='delivered')
        sent_before = AlertDelivery.objects.filter(pk=sent.pk).values().get()
        summary = self.summary()
        summary.save()
        summary_delivery = AlertDelivery.objects.create(event=summary, channel=channel)
        queued = TaskRun.objects.create(task_type='inspection', inspection_profile=self.profile,
                                       target_scope_snapshot={'target_ids': ['queued']},
                                       scope_key='queued', active_scope_key='queued')
        TaskRun.objects.filter(pk=self.task.pk).update(status='success', progress=100,
            finished_at=timezone.now(), active_scope_key=None)
        migration = import_module('net.migrations.0042_task_alert_summaries')
        # Historical migration models have plain managers, as required for data updates.
        from django.db.migrations.loader import MigrationLoader
        state = MigrationLoader(connection).project_state(('net', '0042_task_alert_summaries'))
        migration.retire_device_deliveries(state.apps, connection.schema_editor())
        delivery.refresh_from_db()
        event.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual((delivery.status, delivery.lease_token, delivery.lease_expires_at),
                         ('failed', '', None))
        self.assertTrue(delivery.error_summary)
        self.assertEqual(event.status, 'recorded')
        self.assertIsNotNone(self.task.alert_summary_processed_at)
        for row in (delivery, retry, pending):
            row.refresh_from_db()
            self.assertEqual((row.status, row.next_attempt_at, row.lease_token,
                              row.lease_expires_at, row.delivered_at),
                             ('failed', None, '', None, None))
        self.assertEqual(AlertDelivery.objects.filter(pk=sent.pk).values().get(), sent_before)
        sent_event.refresh_from_db()
        summary_delivery.refresh_from_db()
        queued.refresh_from_db()
        self.assertEqual(sent_event.status, 'delivered')
        self.assertEqual(summary_delivery.status, 'pending')
        self.assertIsNone(queued.alert_summary_processed_at)
