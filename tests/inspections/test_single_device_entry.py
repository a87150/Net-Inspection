from django.test import TestCase
from django.urls import reverse
from net.models import Server, InspectionProfile, TaskRun
from tests.auth import login_admin, login_reader


class SingleDeviceEntryTests(TestCase):
    def setUp(self):
        login_admin(self.client)
        self.first = Server.objects.create(ip='192.0.2.51')
        self.second = Server.objects.create(ip='192.0.2.52')
        self.profile = InspectionProfile.objects.create(name='Both servers', device_type='server', selected_items=['cpu'])

    def test_real_row_form_posts_to_fixed_device_even_for_all_profile(self):
        url = reverse('single_device_task_create', args=['servers', self.first.pk])
        page = self.client.get(reverse('asset_list', args=['servers']), {'task_modal':'run', 'task_single_target':str(self.first.pk)})
        self.assertContains(page, 'action="' + url + '"')
        response = self.client.post(url, {'profile_id':str(self.profile.pk), 'target_mode':'all', 'target_ids':[str(self.second.pk)]})
        self.assertEqual(response.status_code,302)
        task=TaskRun.objects.get()
        self.assertEqual(task.total_targets,1)
        self.assertEqual(list(task.target_runs.values_list('target_id',flat=True)),[str(self.first.pk)])

    def test_profile_switch_retains_single_action_and_bulk_is_separate(self):
        for profile in [self.profile, InspectionProfile.objects.create(name='Other',device_type='server',selected_items=['memory'])]:
            page=self.client.get(reverse('asset_list',args=['servers']),{'task_profile':str(profile.pk),'task_modal':'run','task_single_target':str(self.first.pk)})
            self.assertContains(page, reverse('single_device_task_create',args=['servers',self.first.pk]))
        self.assertNotContains(self.client.get(reverse('asset_list',args=['servers'])), 'action="'+reverse('single_device_task_create',args=['servers',self.first.pk])+'"')

    def test_reader_cannot_enqueue_and_get_does_not_write(self):
        url=reverse('single_device_task_create',args=['servers',self.first.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        login_reader(self.client)
        self.assertEqual(self.client.post(url,{'profile_id':self.profile.pk}).status_code,403)
        self.assertFalse(TaskRun.objects.exists())

    def test_invalid_or_wrong_kind_profile_never_enqueues(self):
        wrong=InspectionProfile.objects.create(name='Network',device_type='network_device',selected_items=['cpu'])
        for profile_id in ['invalid', str(wrong.pk)]:
            response=self.client.post(reverse('single_device_task_create',args=['servers',self.first.pk]),{'profile_id':profile_id})
            self.assertEqual(response.status_code,302)
            self.assertFalse(TaskRun.objects.exists())
