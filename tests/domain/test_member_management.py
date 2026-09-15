from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.urls import reverse

from net.models import Domain_Account, Domain_Computer, Domain_Group, DomainMembership, Domain_Controller_Config, TaskRun
from net.domain.sync import apply_domain_snapshot
from net.domain.client import DomainActionResult
from net.inspections.worker import TaskWorker
from .test_memberships import snapshot, BASE, GROUP


class MemberManagementTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('member-manager', is_staff=True)
        self.client.force_login(self.admin)
        Domain_Controller_Config.objects.create(host='ldap.example.test', base_dn=BASE, bind_username='reader', bind_password='test-only')
        apply_domain_snapshot(snapshot())
        self.group = Domain_Group.objects.get(distinguished_name=GROUP)
        self.url = reverse('domain_group_members', args=[self.group.pk])
        self.alice = Domain_Account.objects.get(login_name='alice')
        self.bob = Domain_Account.objects.create(account_name='Bob', login_name='bob', distinguished_name='CN=Bob,OU=People,' + BASE)
        self.carol = Domain_Account.objects.create(account_name='Carol', login_name='carol', distinguished_name='CN=Carol,OU=People,' + BASE)

    def add(self, ids, object_type='account'):
        return self.client.post(self.url + '?mode=add&object_type=' + object_type, {
            'action': 'add_group', 'object_type': object_type, 'target_ids': [str(pk) for pk in ids]})

    def test_blue_entry_and_add_candidates_exclude_existing_members(self):
        page = self.client.get(reverse('domain_group_list'))
        self.assertContains(page, 'data-modal-load="domainGroupMembersModal">成员管理</a>')
        self.assertContains(page, 'class="btn btn-outline-primary btn-sm" href="' + self.url + '"')
        response = self.client.get(self.url, {'mode': 'add', 'object_type': 'account', 'q': 'Bob'})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertContains(response, 'Bob')
        self.assertNotContains(response, 'value="' + str(self.alice.pk) + '"')

    def test_batch_add_runs_worker_and_updates_names_and_count(self):
        response = self.add([self.bob.pk, self.carol.pk])
        self.assertContains(response, '添加成员任务已入队')
        task = TaskRun.objects.get(task_type='domain_operation')
        self.assertEqual(task.total_targets, 2)
        self.assertEqual(task.parameters_snapshot['group_dn'], GROUP)
        with patch('net.domain.executor.DomainClient.execute', return_value=DomainActionResult(True)) as execute:
            self.assertTrue(TaskWorker(worker_id='member-add', threads=1).run_once())
        self.assertEqual(execute.call_count, 2)
        self.bob.refresh_from_db()
        self.group.refresh_from_db()
        self.assertEqual(self.bob.group_names, 'Operators')
        self.assertEqual(self.group.member_count, 4)

    def test_computer_candidates_and_add_failure_keep_original_members(self):
        pc = Domain_Computer.objects.create(computer_name='PC02', distinguished_name='CN=PC02,OU=Computers,' + BASE)
        page = self.client.get(self.url, {'mode': 'add', 'object_type': 'computer'})
        self.assertContains(page, 'PC02')
        self.add([pc.pk], 'computer')
        with patch('net.domain.executor.DomainClient.execute', return_value=DomainActionResult(False, 'LDAP 权限不足。')):
            TaskWorker(worker_id='member-add', threads=1).run_once()
        self.assertFalse(DomainMembership.objects.filter(group=self.group, computer=pc).exists())
        self.assertEqual(TaskRun.objects.get().status, 'failed')

    def test_forged_or_duplicate_and_existing_targets_are_rejected(self):
        external = Domain_Account.objects.create(account_name='Outside', login_name='outside', distinguished_name='CN=Outside,DC=outside,DC=test')
        for ids in ([self.bob.pk, self.bob.pk], [self.alice.pk], [external.pk], ['invalid-id']):
            self.add(ids)
        self.add([self.bob.pk], 'group')
        self.client.post(self.url, {'action': 'delete_group', 'target_ids': [self.bob.pk]})
        self.assertFalse(TaskRun.objects.exists())

    def test_duplicate_enqueue_and_inactive_group_are_rejected(self):
        self.add([self.bob.pk])
        self.add([self.bob.pk])
        self.assertEqual(TaskRun.objects.count(), 1)
        self.group.is_available = False
        self.group.save(update_fields=['is_available'])
        self.add([self.carol.pk])
        self.assertEqual(TaskRun.objects.count(), 1)

    def test_reader_and_guest_cannot_add(self):
        reader = get_user_model().objects.create_user('member-viewer')
        self.client.force_login(reader)
        self.assertEqual(self.add([self.bob.pk]).status_code, 403)
        self.client.logout()
        self.assertEqual(self.add([self.bob.pk]).status_code, 302)
        self.assertFalse(TaskRun.objects.exists())
