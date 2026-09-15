"""Phase 3 Task 4 alert UI contracts.

Each test names the user-visible regression it prevents.  Transport is left
real for disabled channels so the view cannot accidentally initiate a request.
"""

from unittest.mock import patch
import csv

from tests import response_body
from urllib.parse import parse_qs, urlsplit

from django.test import Client, TestCase
from django.core.management import call_command
from io import StringIO
from django.urls import reverse
from django.utils import timezone

from net.models import (
    AlertChannel,
    AlertDelivery,
    AlertEvent,
    AlertPolicy,
    InspectionProfile,
    Server,
    TaskRun,
)
from net.inspections.queue import enqueue_task


class AlertUiTests(TestCase):
    def setUp(self):
        from tests.auth import login_admin
        login_admin(self.client)
        self.profile = InspectionProfile.objects.create(
            name='UI server profile',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
        )
        self.server = Server.objects.create(name='UI-ALERT-SRV', ip='192.0.2.251')
        self.primary = AlertChannel.objects.create(
            name='primary disabled channel',
            channel_type=AlertChannel.ChannelType.FEISHU,
            is_enabled=False,
            settings={'webhook_url': 'https://open.feishu.test/hook/private-token'},
        )
        self.secondary = AlertChannel.objects.create(
            name='secondary email channel',
            channel_type=AlertChannel.ChannelType.EMAIL,
            is_enabled=False,
            settings={
                'smtp_host': 'smtp.example.test', 'smtp_port': 25,
                'use_tls': False, 'use_ssl': False,
                'from_email': 'ops@example.test', 'recipients': ['ops@example.test'],
                'password': 'mail-password-value',
            },
        )
        self.default_policy = AlertPolicy.objects.create(
            name='default UI policy', is_default=True,
            mode=AlertPolicy.Mode.OVERRIDE,
        )
        self.default_policy.channels.add(self.primary, self.secondary)

    def test_unsaved_project_policy_displays_default_source_and_channels(self):
        response = self.client.get(reverse('asset_list', args=['servers']))
        self.assertEqual(response.context['alert_effective_source_name'], self.default_policy.name)
        self.assertContains(response, 'data-alert-inherited-channels')
        self.assertContains(response, 'data-alert-override-channels')
        self.assertEqual({c.pk for c in response.context['alert_inherited_channels']},
                         {self.primary.pk, self.secondary.pk})

    def target(self):
        task = enqueue_task(self.profile, [self.server.pk], TaskRun.Source.MANUAL)
        target = task.target_runs.get()
        now = timezone.now()
        TaskRun.objects.filter(pk=task.pk).update(
            status=TaskRun.Status.SUCCESS, active_scope_key=None,
            progress=100, completed_targets=1, successful_targets=1,
            finished_at=now,
        )
        target.__class__.objects.filter(pk=target.pk).update(
            status=TaskRun.Status.SUCCESS, started_at=now, finished_at=now,
        )
        target.refresh_from_db()
        return target

    def event(self, *, state='abnormal'):
        from net.alerts.service import process_target_findings

        target = self.target()
        event = process_target_findings(target, [{
            'key': f'ui.{target.pk}', 'severity': 'critical',
            'title': 'UI alert finding', 'detail': 'test-only detail',
            'state': state,
        }])[0]
        return event

    def test_alert_configuration_exposes_multichannel_default_and_safe_secret_forms(self):
        """Rendering stored credentials would expose write-only channel secrets."""
        response = self.client.get(reverse('alert_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '告警配置')
        self.assertContains(response, 'app/js/inspections/task_ui.js')
        self.assertContains(response, 'primary disabled channel')
        self.assertContains(response, 'name="channels"')
        content = response.content.decode()
        self.assertNotIn('private-token', content)
        self.assertNotIn('mail-password-value', content)

    def test_project_policy_popup_saves_inherit_and_multichannel_override(self):
        """Saving selected channels as inherit would disagree with the delivery service."""
        page = self.client.get(reverse('asset_list', args=['servers']))
        self.assertContains(page, '告警配置')
        self.assertContains(page, '继承默认告警策略')

        inherited = self.client.post(reverse('alert_policy_save'), {
            'scope': 'inspection', 'profile_id': str(self.profile.pk),
            'mode': AlertPolicy.Mode.INHERIT,
            'next': reverse('asset_list', args=['servers']),
        })
        self.assertEqual(inherited.status_code, 302)
        policy = AlertPolicy.objects.get(inspection_profile=self.profile)
        self.assertEqual(policy.mode, AlertPolicy.Mode.INHERIT)
        self.assertEqual(policy.effective_channels(), [])
        self.assertEqual(
            set(self.default_policy.channels.values_list('pk', flat=True)),
            {self.primary.pk, self.secondary.pk},
        )

        overridden = self.client.post(reverse('alert_policy_save'), {
            'scope': 'inspection', 'profile_id': str(self.profile.pk),
            'mode': AlertPolicy.Mode.OVERRIDE,
            'channels': [str(self.primary.pk), str(self.secondary.pk)],
            'next': reverse('asset_list', args=['servers']),
        })
        self.assertEqual(overridden.status_code, 302)
        self.assertTrue(self.client.get(overridden['Location']).context['alert_policy_modal_auto_open'])
        policy.refresh_from_db()
        self.assertEqual(policy.mode, AlertPolicy.Mode.OVERRIDE)
        self.assertEqual(set(policy.channels.values_list('pk', flat=True)), {
            self.primary.pk, self.secondary.pk,
        })

    def test_test_send_requires_csrf_uses_saved_channel_and_audits_disabled_result(self):
        """Accepting endpoint fields here would let the browser supply a secret-bearing target."""
        csrf_client = Client(enforce_csrf_checks=True)
        from tests.auth import login_admin
        login_admin(csrf_client)
        config = csrf_client.get(reverse('alert_list'))
        token = config.cookies['csrftoken'].value

        forbidden = csrf_client.post(reverse('alert_test_send'), {
            'channel_id': str(self.primary.pk),
            'webhook_url': 'https://attacker.invalid/secret',
        })
        self.assertEqual(forbidden.status_code, 403)

        with patch('net.alerts.feishu.requests.post') as outbound:
            response = csrf_client.post(
                reverse('alert_test_send'),
                {'channel_id': str(self.primary.pk), 'webhook_url': 'https://attacker.invalid/secret'},
                HTTP_X_CSRFTOKEN=token,
            )
        self.assertEqual(response.status_code, 302)
        outbound.assert_not_called()
        audit = self.primary.test_send_audits.get()
        self.assertEqual(audit.status, 'failed')
        self.assertIn('disabled', audit.response_summary.lower())
        self.assertNotIn('attacker.invalid', audit.response_summary)

    def test_invalid_channel_save_reopens_create_and_edit_without_retaining_secrets(self):
        """A failed save must retain safe fields and reopen the correct channel editor."""
        for data, channel_type, channel_id in [
            ({'channel_type': 'dingtalk', 'name': 'New disabled robot',
              'webhook_url': 'http://invalid.test/submitted-token', 'secret': 'submitted-signature'},
             'dingtalk', ''),
            ({'channel_id': str(self.secondary.pk), 'channel_type': 'feishu',
              'name': 'Edited email', 'smtp_host': 'smtp.example.test', 'smtp_port': '70000',
              'from_email': 'ops@example.test', 'recipients': 'ops@example.test',
              'password': 'submitted-password'}, 'email', str(self.secondary.pk)),
        ]:
            with self.subTest(channel_type=channel_type):
                response = self.client.post(reverse('alert_channel_save'), {
                    **data, 'next': '/alerts/?password=next-secret',
                })
                query = parse_qs(urlsplit(response.url).query)
                self.assertEqual(query.get('alert_modal'), ['channel'])
                self.assertEqual(query.get('alert_channel_type'), [channel_type])
                self.assertEqual(query.get('alert_channel_id', ['']), [channel_id])
                session_text = str(dict(self.client.session))
                reopened = self.client.get(response.url)
                self.assertTrue(reopened.context['alert_channel_modal_auto_open'])
                self.assertEqual(reopened.context['alert_channel_form']['name'].value(), data['name'])
                self.assertContains(reopened, '渠道未保存')
                self.assertTrue(reopened.context['alert_channel_errors'])
                for secret in ('submitted-token', 'submitted-signature', 'submitted-password',
                               'private-token', 'mail-password-value', 'next-secret'):
                    self.assertNotIn(secret, response.url + session_text + reopened.content.decode())
                self.assertNotIn('alert_form_failure', self.client.session)
        self.secondary.refresh_from_db()
        self.assertEqual(self.secondary.name, 'secondary email channel')
        self.assertEqual(AlertChannel.objects.count(), 2)

    def test_invalid_policy_save_reopens_correct_scope_with_safe_selection_and_errors(self):
        """Redirects must not turn a failed project override into the default editor."""
        cases = [
            ({'scope': 'default', 'mode': 'override', 'name': 'x' * 256,
              'channels': [str(self.primary.pk)]}, '/alerts/'),
            ({'scope': 'inspection', 'profile_id': str(self.profile.pk),
              'mode': 'override', 'channels': []}, reverse('asset_list', args=['servers'])),
            ({'scope': 'inspection', 'profile_id': str(self.profile.pk),
              'mode': 'override', 'channels': [str(self.primary.pk), 'invalid-secret-id']},
             'https://attacker.invalid/'),
        ]
        for data, next_url in cases:
            with self.subTest(scope=data['scope'], next_url=next_url):
                response = self.client.post(reverse('alert_policy_save'), {
                    **data, 'next': next_url, 'password': 'discard-policy-password',
                })
                query = parse_qs(urlsplit(response.url).query)
                self.assertEqual(query.get('alert_modal'), ['policy'])
                self.assertEqual(query.get('alert_policy_scope'), [data['scope']])
                self.assertEqual(urlsplit(response.url).netloc, '')
                session_text = str(dict(self.client.session))
                reopened = self.client.get(response.url)
                self.assertTrue(reopened.context['alert_policy_modal_auto_open'])
                self.assertEqual(reopened.context['alert_policy_scope'], data['scope'])
                self.assertEqual(reopened.context['alert_policy_mode'], 'override')
                self.assertEqual(reopened.context['alert_policy_profile_id'], data.get('profile_id', ''))
                self.assertEqual(reopened.context['alert_policy_channel_ids'],
                                 {self.primary.pk} if data['channels'] else set())
                self.assertTrue(reopened.context['alert_policy_errors'])
                self.assertContains(reopened, '告警策略未保存')
                for secret in ('discard-policy-password', 'invalid-secret-id'):
                    self.assertNotIn(secret, response.url + session_text + reopened.content.decode())
        self.assertFalse(AlertPolicy.objects.filter(inspection_profile=self.profile).exists())
        self.default_policy.refresh_from_db()
        self.assertEqual(self.default_policy.name, 'default UI policy')

    def test_invalid_save_return_path_must_have_the_requested_modal(self):
        """A same-host URL without that editor must fall back to the alert page."""
        for path in ('/assets/people/', '/assets/servers/', '/records/unknown/'):
            with self.subTest(path=path):
                response = self.client.post(reverse('alert_channel_save'), {
                    'channel_type': 'feishu', 'name': 'Invalid new channel', 'next': path,
                })
                self.assertEqual(urlsplit(response.url).path, '/alerts/')
                reopened = self.client.get(response.url)
                self.assertTrue(reopened.context['alert_channel_modal_auto_open'])

    def test_alert_list_detail_and_filtered_export_show_one_event_with_delivery_outcome(self):
        """Joining delivery rows without distinct events would duplicate alert records."""
        AlertChannel.objects.filter(pk__in=[self.primary.pk, self.secondary.pk]).update(
            is_enabled=True,
        )
        event = self.event()
        now = timezone.now()
        from net.alerts.service import process_persisted_target
        from net.alerts.task_summaries import process_task_summary
        process_persisted_target(event.target_run)
        event = process_task_summary(event.task)
        AlertDelivery.objects.filter(event=event, channel=self.primary).update(
            status=AlertDelivery.Status.SENT, delivered_at=now,
        )
        AlertDelivery.objects.filter(event=event, channel=self.secondary).update(
            status=AlertDelivery.Status.FAILED,
        )
        AlertEvent.objects.filter(pk=event.pk).update(status=AlertEvent.Status.PARTIAL)

        listing = self.client.get(reverse('alert_list'), {
            'filter_event_type': AlertEvent.EventType.SUMMARY,
            'filter_delivery_outcome': 'sent',
        })
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, '任务总结')
        self.assertEqual(list(listing.context['page_obj'].object_list), [event])
        self.assertContains(listing, '导出筛选结果')

        detail = self.client.get(reverse('alert_detail', args=[event.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'primary disabled channel')
        self.assertContains(detail, 'secondary email channel')
        self.assertContains(detail, '任务详情')
        self.assertNotContains(detail, '目标执行详情')
        self.assertNotContains(detail, 'private-token')
        self.assertNotContains(detail, 'mail-password-value')

        exported = self.client.get(reverse('table_export', args=['alert_events']), {
            'filter_event_type': AlertEvent.EventType.SUMMARY,
            'filter_delivery_outcome': 'sent',
        })
        self.assertEqual(exported.status_code, 200)
        self.assertIn('任务总结', response_body(exported).decode('utf-8-sig'))

    def test_delivery_filter_and_csv_use_channel_rows_independent_of_event_status(self):
        """Aggregate status or a non-distinct delivery join must not change event membership."""
        events = []
        for outcome in ('sent', 'failed', 'retry'):
            event = self.event()
            # All aggregates are identical; each event has two matching deliveries.
            AlertEvent.objects.filter(pk=event.pk).update(status=AlertEvent.Status.PENDING)
            for channel in (self.primary, self.secondary):
                AlertDelivery.objects.create(
                    event=event, channel=channel, status=outcome,
                    delivered_at=timezone.now() if outcome == 'sent' else None,
                    next_attempt_at=timezone.now() if outcome == 'retry' else None,
                )
            events.append(event)
        for outcome, event, label in zip(('sent', 'failed', 'retry'), events,
                                         ('发送成功', '发送失败', '等待重试')):
            with self.subTest(outcome=outcome):
                params = {'filter_delivery_outcome': outcome, 'page_size': 20}
                response = self.client.get(reverse('alert_list'), params)
                self.assertEqual(list(response.context['page_obj'].object_list), [event])
                self.assertContains(response, f'primary disabled channel: {label}')
                self.assertContains(response, f'secondary email channel: {label}')
                exported = self.client.get(reverse('table_export', args=['alert_events']), params)
                rows = list(csv.reader(StringIO(response_body(exported).decode('utf-8-sig'))))
                self.assertEqual(len(rows), 2)
                outcomes = rows[1][rows[0].index('渠道结果')]
                self.assertIn(f'primary disabled channel: {label}', outcomes)
                self.assertIn(f'secondary email channel: {label}', outcomes)

    def test_task_detail_exposes_pre_event_alert_processing_error_without_fake_event(self):
        """Hiding retryable processing diagnostics would make a failed alert pass invisible."""
        target = self.target()
        target.__class__.objects.filter(pk=target.pk).update(
            alert_processing_error='ValueError: alert processor test failure',
            alert_attempted_at=timezone.now(),
            alert_processed_at=None,
        )

        response = self.client.get(reverse('task_detail', args=[target.task_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '告警处理待重试')
        self.assertContains(response, 'alert processor test failure')
        self.assertEqual(target.alert_events.count(), 0)

    @patch('net.alerts.feishu.requests.post')
    def test_demo_seed_creates_repeatable_disabled_alert_history_without_outbound_calls(self, outbound):
        """Demo setup must never turn saved webhook examples into live transport attempts."""
        call_command('seed_demo_data', reset=True, stdout=StringIO())
        first = list(AlertEvent.objects.order_by('pk').values_list('id', 'event_type', 'status'))
        channels = AlertChannel.objects.filter(name__startswith='演示告警渠道')

        self.assertEqual(channels.count(), 3)
        self.assertFalse(channels.filter(is_enabled=True).exists())
        self.assertTrue(AlertPolicy.objects.filter(is_default=True).exists())
        self.assertTrue(AlertPolicy.objects.filter(inspection_profile__name='演示告警巡检配置').exists())
        self.assertEqual({event_type for _, event_type, _ in first}, {'abnormal', 'recovery'})
        self.assertTrue(AlertDelivery.objects.filter(status=AlertDelivery.Status.SENT).exists())
        self.assertTrue(AlertDelivery.objects.filter(status=AlertDelivery.Status.FAILED).exists())
        outbound.assert_not_called()

        call_command('seed_demo_data', reset=True, stdout=StringIO())
        self.assertEqual(
            list(AlertEvent.objects.order_by('pk').values_list('id', 'event_type', 'status')),
            first,
        )
