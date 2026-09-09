"""Offline contracts for summary rendering and the isolated settings dialog."""
from types import SimpleNamespace

from django.test import Client, SimpleTestCase, TestCase

from net.alerts.messages import build_alert_message


class FrozenSummaryTests(SimpleTestCase):
    def test_summary_uses_frozen_message_and_task_link_without_database(self):
        event = SimpleNamespace(event_type='summary', task_id='task-123', findings=[],
            profile_type='server', profile_id='1', target_type='', target_id='',
            occurred_at='2026-09-09', id='event-1', summary='', summary_data={
            'message_title': '固定标题', 'message_text': '固定正文',
            'details_url': '/tasks/task-123/',
        })
        message = build_alert_message(event)
        self.assertEqual(message.title, '固定标题')
        self.assertEqual(message.text, '固定正文')
        self.assertEqual(message.detail_url, '/tasks/task-123/')

    def test_missing_frozen_message_uses_defaults_without_database(self):
        event = SimpleNamespace(event_type='summary', task_id='task-123', findings=[],
            profile_type='server', profile_id='1', target_type='', target_id='',
            occurred_at='2026-09-09', id='event-1', summary='', summary_data={
                'task_name': '服务器巡检', 'total': 7, 'details_url': '/tasks/task-123/'})
        self.assertIn('服务器巡检', build_alert_message(event).title)


class RenderingTests(SimpleTestCase):
    def test_summary_detail_shows_frozen_text_without_target_link(self):
        from django.template.loader import render_to_string
        from uuid import UUID
        event = SimpleNamespace(event_type='summary', task_id=UUID(int=1), profile_type='server',
            get_event_type_display='任务总结', get_status_display='待发送', summary='',
            summary_data={'message_title': '固定总结', 'message_text': '<script>unsafe</script>\n正常 7',
                          'total': 10, 'normal': 7, 'abnormal': 2})
        html = render_to_string('alerts/detail.html', {'event': event})
        self.assertIn('固定总结', html)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;', html)
        self.assertNotIn('filter_target_id', html)

    def test_custom_fields_and_empty_fields_use_selected_mode(self):
        from net.alerts.templates import render_task_summary
        data = {'task_name': '夜间巡检', 'total': 7, 'issues': ['磁盘满'],
                'details_url': '/tasks/123/'}
        result = render_task_summary(data, {'mode': 'detailed', 'title_template': '{task_name} {total}'})
        self.assertEqual(result['title'], '夜间巡检 7')
        self.assertIn('磁盘满', result['text'])
        self.assertIn('/tasks/123/', result['text'])
        self.assertNotIn('磁盘满', render_task_summary(data, {})['text'])

    def test_templates_reject_anything_except_simple_known_variables(self):
        from index.alerts.templates import AlertNotificationTemplateForm
        for value in ('{unknown}', '{task_name.__class__}', '{issues[0]}',
                      '{total:03}', '{total:}', '{total!r}', '{}', '{', '{{task_name}}'):
            with self.subTest(value=value):
                form = AlertNotificationTemplateForm({'mode': 'compact', 'title_template': value})
                self.assertFalse(form.is_valid())
                self.assertIn('title_template', form.errors)
        form = AlertNotificationTemplateForm({'mode': 'invalid'})
        self.assertFalse(form.is_valid())
        self.assertIn('mode', form.errors)

    def test_bounded_unicode_output_preserves_details_link_and_redacts_secrets(self):
        from net.alerts.templates import render_task_summary
        result = render_task_summary({'task_name': '长' * 5000,
            'issues': [{'title': '错误', 'detail': 'password=secret ' + '长' * 10000}] * 100,
            'details_url': '/tasks/123/'},
            {'mode': 'detailed', 'body_template': '{issues}' * 100})
        self.assertLessEqual(len(result['title']), 255)
        self.assertLessEqual(len(result['text'].encode('utf-8')), 12000)
        self.assertIn('/tasks/123/', result['text'])
        self.assertNotIn('password=secret', result['text'])

    def test_variable_values_are_not_evaluated_recursively(self):
        from net.alerts.templates import render_task_summary
        result = render_task_summary({'task_name': '{unknown}'}, {'title_template': '{task_name}'})
        self.assertEqual(result['title'], '{unknown}')

    def test_long_details_url_is_preserved(self):
        from net.alerts.templates import render_task_summary
        url = '/tasks/' + 'a' * 1800 + '/'
        result = render_task_summary({'details_url': url}, {})
        self.assertTrue(result['text'].endswith(url))

    def test_wire_json_also_bounded_for_unicode_and_control_characters(self):
        import json
        from net.alerts.templates import render_task_summary
        for value in ('😀' * 4000, '\x01' * 4000, '长' * 4000):
            result = render_task_summary({'details_url': '/tasks/123/'}, {'body_template': value})
            self.assertLess(len(json.dumps({'text': result['text']}).encode()), 18000)

    def test_multiline_examples_are_limited_to_five_items(self):
        from net.alerts.templates import render_task_summary
        result = render_task_summary({'issues': '\n'.join(f'issue-{i}' for i in range(10)),
                                     'details_url': '/tasks/123/'}, {'mode': 'detailed'})
        self.assertIn('issue-4', result['text'])
        self.assertNotIn('issue-5', result['text'])
        self.assertIn('任务详情', result['text'])


class TemplateSettingsTests(TestCase):
    def setUp(self):
        from tests.auth import login_admin
        self.admin = login_admin(self.client)
        self.url = '/alerts/template/'

    def test_get_and_default_render_do_not_create_singleton(self):
        from net.models import AlertNotificationTemplate
        from net.alerts.templates import render_task_summary
        response = self.client.get(self.url)
        self.assertContains(response, 'data-modal-form')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('巡检', render_task_summary({'task_name': '巡检'})['title'])
        self.assertFalse(AlertNotificationTemplate.objects.exists())

    def test_preview_neither_saves_nor_sends_and_save_stays_in_modal(self):
        from unittest.mock import patch
        from net.models import AlertNotificationTemplate
        with patch('requests.sessions.Session.request', side_effect=AssertionError('External HTTP forbidden')), \
                patch('smtplib.SMTP', side_effect=AssertionError('External SMTP forbidden')):
            response = self.client.post(self.url, {'mode': 'detailed', 'action': 'preview',
                'title_template': '预览 {task_name}'})
            self.assertContains(response, '预览 示例服务器巡检')
            self.assertFalse(AlertNotificationTemplate.objects.exists())
            response = self.client.post(self.url, {'mode': 'compact', 'action': 'save',
                'title_template': '已保存 {task_name}'})
            self.assertContains(response, '已保存')
            self.assertContains(response, 'id="alertTemplateModal"')
            self.assertNotIn('Location', response)
        self.assertEqual(AlertNotificationTemplate.objects.get(key='task_summary').title_template,
                         '已保存 {task_name}')

    def test_invalid_input_visible_without_write(self):
        from net.models import AlertNotificationTemplate
        for data in ({'mode': 'bad'}, {'mode': 'compact', 'body_template': '{not_allowed}'},
                     {'mode': 'compact', 'action': 'send'}):
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, 'errorlist', status_code=400)
        self.assertFalse(AlertNotificationTemplate.objects.exists())

    def test_permissions_methods_and_csrf(self):
        from tests.auth import login_reader
        self.assertEqual(self.client.put(self.url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post(self.url, {'mode': 'compact'}).status_code, 403)
        login_reader(self.client)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'mode': 'compact'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_live_configuration_only_affects_new_render(self):
        from net.models import AlertNotificationTemplate
        from net.alerts.templates import render_task_summary
        template = AlertNotificationTemplate.objects.create(key='task_summary',
            mode='compact', title_template='第一版 {task_name}')
        frozen = render_task_summary({'task_name': '巡检'})
        template.title_template = '第二版 {task_name}'
        template.save()
        event = SimpleNamespace(event_type='summary', task_id='123', summary_data={
            'task_name': '巡检', 'message_title': frozen['title'], 'message_text': frozen['text']})
        with self.assertNumQueries(0):
            message = build_alert_message(event)
        self.assertEqual(message.title, '第一版 巡检')
        self.assertEqual(render_task_summary({'task_name': '巡检'})['title'], '第二版 巡检')
