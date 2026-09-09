import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from index.alerts.forms import DingTalkAlertChannelForm, FeishuAlertChannelForm
from net.models import (
    AlertChannel, AlertDelivery, AlertEvent, AlertPolicy, AlertState,
    ComputerAnalysisProfile, InspectionProfile, TaskRun, TaskTargetRun,
)


class SummaryDeliveryLeaseTests(TestCase):
    def setUp(self):
        from net.alerts.service import process_target_findings
        from net.alerts.task_summaries import process_task_summary

        profile = InspectionProfile.objects.create(name='summary leases', device_type='server')
        self.channel = AlertChannel.objects.create(name='lease channel', channel_type='feishu')
        policy = AlertPolicy.objects.create(name='lease policy', inspection_profile=profile, mode='override')
        policy.channels.add(self.channel)
        now = timezone.now()
        task = TaskRun.objects.create(
            task_type='inspection', inspection_profile=profile, status='success',
            profile_snapshot={'id': str(profile.pk), 'device_type': 'server'},
            finished_at=now, progress=100, total_targets=1,
            completed_targets=1, successful_targets=1,
        )
        target = TaskTargetRun.objects.create(
            task=task, target_type='server', target_id='lease-server',
            status='success', finished_at=now,
        )
        self.audit = process_target_findings(target, [{
            'key': 'cpu', 'severity': 'warning', 'title': 'CPU', 'state': 'abnormal',
        }])[0]
        TaskTargetRun.objects.filter(pk=target.pk).update(alert_processed_at=now)
        self.event = process_task_summary(task)
        self.assertIsNotNone(self.event)
        self.assertEqual(self.event.event_type, 'summary')
        self.delivery = self.event.deliveries.get()

    def test_active_lease_is_exclusive_and_expired_lease_fences_old_result(self):
        from net.alerts.base import DeliveryResult
        from net.alerts.service import _claim_deliveries, _finish_delivery

        now = timezone.now()
        delivery_id, old_token = _claim_deliveries(event=self.event, now=now)[0]
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, 'sending')
        self.assertEqual(self.delivery.attempt_count, 1)
        self.assertEqual(_claim_deliveries(event=self.event, now=now), [])
        new_id, new_token = _claim_deliveries(
            event=self.event, now=self.delivery.lease_expires_at + timedelta(seconds=1),
        )[0]
        self.assertEqual(new_id, delivery_id)
        self.assertNotEqual(new_token, old_token)
        _finish_delivery(delivery_id, old_token, DeliveryResult(True, 'stale accepted', False))
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, 'sending')
        self.assertEqual(self.delivery.lease_token, new_token)
        self.assertEqual(self.delivery.attempt_count, 2)
        self.assertIsNone(self.delivery.delivered_at)
        _finish_delivery(new_id, new_token, DeliveryResult(True, 'accepted', False))
        self.delivery.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.delivery.status, 'sent')
        self.assertEqual(self.event.status, 'delivered')
        self.assertEqual(self.delivery.response_summary, 'accepted')
        self.assertEqual(_claim_deliveries(event=self.event), [])

    def test_expired_final_attempt_fails_without_another_claim(self):
        from net.alerts.service import _claim_deliveries

        now = timezone.now()
        for attempt in range(self.delivery.max_attempts):
            self.assertEqual(len(_claim_deliveries(event=self.event, now=now)), 1)
            self.delivery.refresh_from_db()
            self.assertEqual(self.delivery.attempt_count, attempt + 1)
            now = self.delivery.lease_expires_at + timedelta(seconds=1)
        self.assertEqual(_claim_deliveries(event=self.event, now=now), [])
        self.delivery.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.delivery.status, 'failed')
        self.assertEqual(self.event.status, 'failed')
        self.assertEqual(self.delivery.attempt_count, self.delivery.max_attempts)
        self.assertIsNone(self.delivery.lease_expires_at)
        self.assertEqual(self.delivery.lease_token, '')

    def test_claim_skips_legacy_device_delivery_even_when_explicitly_requested(self):
        from net.alerts.service import _claim_deliveries

        legacy = AlertDelivery.objects.create(event=self.audit, channel=self.channel)
        self.assertEqual(_claim_deliveries(event=self.audit), [])
        claimed = _claim_deliveries(limit=100)
        self.assertEqual([delivery_id for delivery_id, _ in claimed], [self.delivery.pk])
        legacy.refresh_from_db()
        self.audit.refresh_from_db()
        self.assertEqual(legacy.status, 'pending')
        self.assertEqual(legacy.attempt_count, 0)
        self.assertEqual(self.audit.status, 'recorded')


class WebhookCredentialTests(TestCase):
    def test_existing_webhook_is_write_only_and_blank_edit_preserves_it(self):
        for kind, form_class in [('feishu', FeishuAlertChannelForm), ('dingtalk', DingTalkAlertChannelForm)]:
            with self.subTest(kind=kind):
                url = 'https://example.com/hooks/private-path?access_token=stored-private-token'
                channel = AlertChannel.objects.create(
                    name=kind, channel_type=kind, settings={'webhook_url': url, 'secret': 'saved-signing-key'},
                )
                initial = form_class(instance=channel)
                self.assertEqual(initial.initial['webhook_url'], '••••••••')
                self.assertNotIn('stored-private-token', initial.as_p())
                form = form_class(instance=channel, data={'name': kind, 'is_enabled': 'on', 'webhook_url': '', 'secret': ''})
                self.assertTrue(form.is_valid(), form.errors)
                form.save()
                channel.refresh_from_db()
                self.assertEqual(channel.settings, {'webhook_url': url, 'secret': 'saved-signing-key'})

    def test_replacement_is_saved_without_being_rendered_even_on_invalid_forms(self):
        for kind, form_class in [('feishu', FeishuAlertChannelForm), ('dingtalk', DingTalkAlertChannelForm)]:
            for url in ('http://example.com/?access_token=replacement-token', 'https://[replacement-token'):
                with self.subTest(kind=kind, malformed=url.startswith('https')):
                    form = form_class(data={'name': kind, 'webhook_url': url, 'secret': 'replacement-secret'})
                    self.assertFalse(form.is_valid())
                    for output in (form.as_p(), str(form.errors), repr(form.initial)):
                        self.assertNotIn('replacement-token', output)
                        self.assertNotIn('replacement-secret', output)
            valid = form_class(data={'name': kind, 'webhook_url': 'https://example.com/?access_token=new-private-token'})
            self.assertTrue(valid.is_valid(), valid.errors)
            self.assertNotIn('new-private-token', valid.as_p())
            self.assertEqual(valid.save().settings['webhook_url'], 'https://example.com/?access_token=new-private-token')

    def test_new_channel_requires_webhook_and_explicit_initials_cannot_expose_secrets(self):
        for form_class in (FeishuAlertChannelForm, DingTalkAlertChannelForm):
            form = form_class(data={'name': 'new', 'webhook_url': ''})
            self.assertFalse(form.is_valid())
            self.assertIn('webhook_url', form.errors)
            initial = form_class(initial={'webhook_url': 'private-token', 'secret': 'private-signature'})
            self.assertNotIn('webhook_url', initial.initial)
            self.assertNotIn('secret', initial.initial)


class AlertScopeAndConstraintTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(name='server-alerts', device_type='server')
        self.other_profile = InspectionProfile.objects.create(name='other-alerts', device_type='server')
        self.task = TaskRun.objects.create(
            task_type='inspection', inspection_profile=self.profile,
            profile_snapshot={'id': str(self.profile.pk), 'device_type': 'server'},
        )
        self.target = TaskTargetRun.objects.create(task=self.task, target_type='server', target_id='server-one')
        self.channel = AlertChannel.objects.create(name='test-channel', channel_type='feishu', settings={'webhook_url': 'https://example.com/hook'})

    def event_values(self, **overrides):
        return dict({
            'task': self.task, 'target_run': self.target,
            'profile_type': 'inspection_profile', 'profile_id': str(self.profile.pk),
            'target_type': 'server', 'target_id': 'server-one',
            'event_type': 'abnormal', 'findings': [{'key': 'cpu', 'severity': 'warning', 'title': 'CPU'}],
        }, **overrides)

    def state_values(self, **overrides):
        return dict({
            'profile_type': 'inspection_profile', 'profile_id': str(self.profile.pk),
            'target_type': 'server', 'target_id': 'server-one', 'finding_key': 'cpu',
        }, **overrides)

    def test_event_rejects_forged_profile_target_task_and_policy_without_writes(self):
        unrelated = AlertPolicy.objects.create(name='unrelated', inspection_profile=self.other_profile)
        other_task = TaskRun.objects.create(task_type='inspection', inspection_profile=self.other_profile)
        for fields in (
            {'profile_type': 'computer_analysis_profile'}, {'profile_id': str(self.other_profile.pk)},
            {'target_type': 'monitor'}, {'target_id': 'server-other'}, {'task': other_task}, {'policy': unrelated},
        ):
            with self.subTest(fields=list(fields)):
                with transaction.atomic(), self.assertRaises(ValidationError):
                    AlertEvent.objects.create(**self.event_values(**fields))
                self.assertEqual(AlertEvent.objects.count(), 0)

    def test_event_derives_identity_from_persisted_task_target_not_mutated_objects(self):
        self.target.target_id = 'unsaved-forgery'
        self.task.inspection_profile = self.other_profile
        values = self.event_values()
        for field in ('profile_type', 'profile_id', 'target_type', 'target_id'):
            values.pop(field)
        event = AlertEvent.objects.create(**values)
        self.assertEqual((event.profile_type, event.profile_id, event.target_type, event.target_id),
                         ('inspection_profile', str(self.profile.pk), 'server', 'server-one'))

    def test_default_and_matching_policies_are_accepted_and_existing_scope_cannot_move(self):
        default = AlertPolicy.objects.create(name='default', is_default=True, mode='override')
        matching = AlertPolicy.objects.create(name='matching', inspection_profile=self.profile)
        for event_type, policy in [('abnormal', default), ('recovery', matching)]:
            event = AlertEvent.objects.create(**self.event_values(event_type=event_type, policy=policy))
            event.target_id = 'another-target'
            with self.assertRaises(ValidationError):
                event.save(update_fields=['target_id'])
            event.refresh_from_db()
            self.assertEqual(event.target_id, 'server-one')

    def test_analysis_event_uses_analysis_profile_identity(self):
        profile = ComputerAnalysisProfile.objects.create(name='analysis-profile')
        task = TaskRun.objects.create(task_type='computer_analysis', analysis_profile=profile, profile_snapshot={'id': str(profile.pk)})
        target = TaskTargetRun.objects.create(task=task, target_type='computer_log', target_id='42')
        event = AlertEvent.objects.create(task=task, target_run=target, event_type='abnormal')
        self.assertEqual((event.profile_type, event.profile_id, event.target_type, event.target_id),
                         ('computer_analysis_profile', str(profile.pk), 'computer_log', '42'))

    def test_referenced_policy_scope_changes_are_rejected_without_breaking_history(self):
        analysis = ComputerAnalysisProfile.objects.create(name='other-analysis')
        cases = [
            (False, {'inspection_profile': self.other_profile}),
            (False, {'inspection_profile_id': self.other_profile.pk}),
            (False, {'inspection_profile': None, 'analysis_profile': analysis}),
            (False, {'inspection_profile': None, 'analysis_profile_id': analysis.pk}),
            (False, {'inspection_profile': None, 'is_default': True, 'default_slot': 'default'}),
            (True, {'is_default': False, 'default_slot': None, 'inspection_profile': self.other_profile}),
        ]
        for is_default, changes in cases:
            for path in ('save', 'partial_save', 'update', 'bulk_update', 'bulk_upsert'):
                with self.subTest(default=is_default, fields=list(changes), path=path), transaction.atomic():
                    try:
                        policy = AlertPolicy.objects.create(
                            name='referenced', is_default=is_default, mode='override',
                            inspection_profile=None if is_default else self.profile,
                        )
                        policy.channels.add(self.channel)
                        policy.full_clean()
                        event = AlertEvent.objects.create(**self.event_values(policy=policy))
                        state = AlertState.objects.create(**self.state_values(last_event=event))
                        event.states.add(state)
                        original = AlertPolicy.objects.values().get(pk=policy.pk)
                        for field, value in changes.items():
                            setattr(policy, field, value)
                        fields = list(changes)
                        with self.assertRaises(ValidationError), transaction.atomic():
                            if path == 'save':
                                policy.save()
                            elif path == 'partial_save':
                                policy.save(update_fields=fields)
                            elif path == 'update':
                                AlertPolicy.objects.filter(pk=policy.pk).update(**changes)
                            elif path == 'bulk_update':
                                AlertPolicy.objects.bulk_update([policy], fields, batch_size=1)
                            else:
                                # Positional options must not bypass the upsert guard.
                                AlertPolicy.objects.bulk_create([policy], 1, False, True, fields, ['pk'])
                        self.assertEqual(AlertPolicy.objects.values().get(pk=policy.pk), original)
                        event.refresh_from_db()
                        self.assertEqual(event.policy_id, policy.pk)
                        self.assertEqual(event.profile_id, str(self.profile.pk))
                        self.assertEqual(list(event.states.all()), [state])
                        event.status = 'sending'
                        event.save(update_fields=['status'])
                        extra = AlertState.objects.create(**self.state_values(finding_key='memory'))
                        event.states.add(extra)
                        extra.last_event = event
                        extra.save(update_fields=['last_event'])
                        event.refresh_from_db()
                        self.assertEqual(event.status, 'sending')
                        self.assertCountEqual(event.states.all(), [state, extra])
                    finally:
                        transaction.set_rollback(True)

    def test_referenced_policy_allows_name_channels_and_same_profile_mode_edits(self):
        default = AlertPolicy.objects.create(name='default', is_default=True, mode='override')
        default.channels.add(self.channel)
        policy = AlertPolicy.objects.create(name='project', inspection_profile=self.profile)
        event = AlertEvent.objects.create(**self.event_values(policy=policy))
        policy.name = 'renamed'
        policy.mode = 'override'
        policy.channels.add(self.channel)
        policy.full_clean()
        policy.save(update_fields=['name', 'mode'])
        self.assertEqual(policy.effective_channels(), [self.channel])
        policy.channels.clear()
        AlertPolicy.objects.filter(pk=policy.pk).update(name='inheriting', mode='inherit')
        policy.refresh_from_db()
        policy.full_clean()
        self.assertEqual(policy.effective_channels(), [self.channel])
        policy.name = 'bulk-renamed'
        AlertPolicy.objects.bulk_update([policy], ['name'])
        policy.name = 'upsert-renamed'
        AlertPolicy.objects.bulk_create(
            [policy], update_conflicts=True, update_fields=['name'], unique_fields=['pk'],
        )
        policy.refresh_from_db()
        self.assertEqual(policy.name, 'upsert-renamed')
        self.assertEqual(policy.inspection_profile_id, self.profile.pk)
        event.status = 'sending'
        event.save(update_fields=['status'])
        state = AlertState.objects.create(**self.state_values(last_event=event))
        state.events.add(event)
        self.assertEqual(list(event.states.all()), [state])

    def test_missing_target_or_inconsistent_snapshot_is_validation_error(self):
        with self.assertRaises(ValidationError):
            AlertEvent.objects.create(**self.event_values(target_run_id=uuid.uuid4(), target_run=None))
        TaskRun.objects.filter(pk=self.task.pk).update(profile_snapshot={'id': str(self.other_profile.pk)})
        with self.assertRaises(ValidationError):
            AlertEvent.objects.create(**self.event_values())
        self.assertEqual(AlertEvent.objects.count(), 0)

    def test_state_last_event_and_m2m_links_reject_cross_scope_in_both_directions(self):
        event = AlertEvent.objects.create(**self.event_values())
        good = AlertState.objects.create(**self.state_values())
        bad = AlertState.objects.create(**self.state_values(target_id='other'))
        with self.assertRaises(ValidationError):
            AlertState.objects.create(**self.state_values(finding_key='memory', target_id='other', last_event=event))
        self.assertEqual(AlertState.objects.count(), 2)
        bad.last_event = event
        with self.assertRaises(ValidationError):
            bad.save(update_fields=['last_event'])
        bad.refresh_from_db()
        self.assertIsNone(bad.last_event_id)
        for action in (lambda: event.states.add(good, bad), lambda: bad.events.add(event)):
            with self.assertRaises(ValidationError), transaction.atomic():
                action()
            self.assertFalse(event.states.exists())
        event.states.add(good)
        with self.assertRaises(ValidationError), transaction.atomic():
            event.states.set([bad])
        self.assertEqual(list(event.states.all()), [good])
        good.last_event = event
        good.save()
        good.target_id = 'other'
        with self.assertRaises(ValidationError):
            good.save()
        good.refresh_from_db()
        self.assertEqual(good.target_id, 'server-one')

    def test_database_rejects_invalid_enums_on_create_and_update(self):
        event = AlertEvent.objects.create(**self.event_values())
        state = AlertState.objects.create(**self.state_values())
        policy = AlertPolicy.objects.create(name='matching', inspection_profile=self.profile)
        delivery = AlertDelivery.objects.create(event=event, channel=self.channel)
        cases = [
            (AlertState, self.state_values(finding_key='memory', status='unknown'), state, {'status': 'unknown'}),
            (AlertEvent, self.event_values(event_type='unknown'), event, {'event_type': 'unknown'}),
            (AlertEvent, self.event_values(event_type='recovery', status='unknown'), event, {'status': 'unknown'}),
            (AlertChannel, {'name': 'invalid', 'channel_type': 'unknown', 'settings': {}}, self.channel, {'channel_type': 'unknown'}),
            (AlertPolicy, {'name': 'invalid', 'inspection_profile': self.other_profile, 'mode': 'unknown'}, policy, {'mode': 'unknown'}),
            (AlertDelivery, {'event': event, 'channel': AlertChannel.objects.create(name='second', channel_type='email'), 'status': 'unknown'}, delivery, {'status': 'unknown'}),
        ]
        for model, create_values, row, update in cases:
            with self.subTest(model=model.__name__, update=update):
                before = model.objects.count()
                with transaction.atomic(), self.assertRaises(IntegrityError):
                    model.objects.create(**create_values)
                self.assertEqual(model.objects.count(), before)
                original = model.objects.values(*update).get(pk=row.pk)
                with transaction.atomic(), self.assertRaises(IntegrityError):
                    model.objects.filter(pk=row.pk).update(**update)
                self.assertEqual(model.objects.values(*update).get(pk=row.pk), original)

    def test_queryset_and_bulk_writes_cannot_bypass_scope_guards(self):
        event = AlertEvent.objects.create(**self.event_values())
        state = AlertState.objects.create(**self.state_values())
        for model, row, values in (
            (AlertEvent, event, {'target_id': 'other'}),
            (AlertEvent, event, {'profile_id': str(self.other_profile.pk)}),
            (AlertState, state, {'target_id': 'other'}),
            (AlertState, state, {'last_event': event}),
        ):
            with self.subTest(model=model.__name__, fields=list(values)):
                with transaction.atomic(), self.assertRaises(ValidationError):
                    model.objects.filter(pk=row.pk).update(**values)
                row.refresh_from_db()
                self.assertEqual(row.target_id, 'server-one')
        second_target = TaskTargetRun.objects.create(task=self.task, target_type='server', target_id='server-two')
        with transaction.atomic(), self.assertRaises(ValidationError):
            AlertEvent.objects.bulk_create([
                AlertEvent(**self.event_values(event_type='recovery')),
                AlertEvent(**self.event_values(target_run=second_target, target_id='other')),
            ])
        self.assertEqual(AlertEvent.objects.count(), 1)
        with transaction.atomic(), self.assertRaises(ValidationError):
            AlertState.objects.bulk_create([
                AlertState(**self.state_values(finding_key='memory')),
                AlertState(**self.state_values(target_id='other', last_event=event)),
            ])
        self.assertEqual(AlertState.objects.count(), 1)

    def test_database_enforces_delivery_timestamp_shape_on_create_and_update(self):
        event = AlertEvent.objects.create(**self.event_values())
        now = timezone.now()
        invalid = [
            {'status': 'sent'}, {'status': 'retry'},
            {'status': 'pending', 'delivered_at': now},
            {'status': 'sent', 'delivered_at': now, 'next_attempt_at': now},
            {'status': 'failed', 'next_attempt_at': now},
            {'status': 'retry', 'next_attempt_at': now, 'attempt_count': 3},
        ]
        for values in invalid:
            with self.subTest(values=list(values), status=values['status']):
                with transaction.atomic(), self.assertRaises(IntegrityError):
                    AlertDelivery.objects.create(event=event, channel=self.channel, **values)
                self.assertFalse(AlertDelivery.objects.exists())
        delivery = AlertDelivery.objects.create(event=event, channel=self.channel)
        for values in invalid:
            with self.subTest(update_status=values['status']):
                with transaction.atomic(), self.assertRaises(IntegrityError):
                    AlertDelivery.objects.filter(pk=delivery.pk).update(**values)
                delivery.refresh_from_db()
                self.assertEqual(delivery.status, 'pending')
        AlertDelivery.objects.filter(pk=delivery.pk).update(status='retry', next_attempt_at=now, attempt_count=1)
        AlertDelivery.objects.filter(pk=delivery.pk).update(status='sent', delivered_at=now, next_attempt_at=None)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, 'sent')
