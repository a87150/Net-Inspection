"""The tasks page carries the global task-history retention setting."""
from django.test import TestCase
from django.urls import reverse

from net.models import TaskHistoryConfig, TaskRun
from tests.auth import login_admin, login_reader


class TaskHistorySettingsTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def test_default_window_is_ninety_days(self):
        self.assertEqual(TaskHistoryConfig.current_days(), 90)

    def test_admin_can_change_the_window_and_it_persists(self):
        response = self.client.post(reverse('task_history_settings'),
                                    {'retention_days': '30', 'next': '/tasks/'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TaskHistoryConfig.current_days(), 30)

    def test_repeating_the_save_updates_rather_than_duplicating(self):
        for value in ('30', '180', '90'):
            self.client.post(reverse('task_history_settings'), {'retention_days': value})
        self.assertEqual(TaskHistoryConfig.objects.count(), 1)
        self.assertEqual(TaskHistoryConfig.current_days(), 90)

    def test_an_undeclared_window_is_rejected(self):
        self.client.post(reverse('task_history_settings'), {'retention_days': '1'})
        self.assertEqual(TaskHistoryConfig.current_days(), 90)

    def test_the_button_and_modal_render_for_admins(self):
        body = self.client.get(reverse('task_list')).content.decode()
        self.assertIn('保留设置', body)
        self.assertIn('id="taskHistoryModal"', body)
        for value in ('30', '90', '180'):
            self.assertIn('value="%s"' % value, body)
        self.assertIn(reverse('task_history_settings'), body)

    def test_a_reader_sees_no_setting_button(self):
        self.client.logout()
        login_reader(self.client)
        body = self.client.get(reverse('task_list')).content.decode()
        self.assertNotIn('保留设置', body)
        self.assertNotIn('id="taskHistoryModal"', body)
        # ...and cannot post the change either.
        response = self.client.post(reverse('task_history_settings'),
                                    {'retention_days': '30'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TaskHistoryConfig.current_days(), 90)

    def test_the_window_only_governs_finished_tasks(self):
        # The page states the rule; assert the button context reflects the config.
        TaskHistoryConfig.objects.update_or_create(pk=1, defaults={'retention_days': '180'})
        body = self.client.get(reverse('task_list')).content.decode()
        at = body.index('value="180"')
        self.assertIn('selected', body[at:at + 40])
        self.assertEqual(TaskRun.objects.count(), 0)
