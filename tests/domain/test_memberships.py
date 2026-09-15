from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TransactionTestCase, SimpleTestCase
from django.urls import reverse
from ldap3 import MODIFY_DELETE

from net.models import (Domain_Account, Domain_Computer, Domain_Group, DomainOU,
                        DomainMembership, Domain_Controller_Config, TaskRun)
from net.domain.sync import apply_domain_snapshot, fetch_domain_snapshot
from net.domain.tasks import enqueue_domain_operation
from net.domain.client import DomainClient, DomainActionResult
from net.inspections.worker import TaskWorker

BASE = 'DC=example,DC=test'
USER = 'CN=Alice,OU=People,' + BASE
PC = 'CN=PC01,OU=Computers,' + BASE
GROUP = 'CN=Operators,OU=Groups,' + BASE
PRIMARY = 'CN=Domain Users,CN=Users,' + BASE
OU = 'OU=Empty,' + BASE


def snapshot():
    return ([{'sAMAccountName': 'alice', 'displayName': 'Alice', 'distinguishedName': USER,
              'objectSid': 'S-1-5-21-1-2-3-1000', 'primaryGroupID': 513}],
            [{'name': 'PC01', 'distinguishedName': PC, 'objectSid': 'S-1-5-21-1-2-3-1001', 'primaryGroupID': 515}],
            [{'name': 'Operators', 'distinguishedName': GROUP, 'member': [USER, PC],
              'objectSid': 'S-1-5-21-1-2-3-1200', 'groupType': -2147483646},
             {'name': 'Domain Users', 'distinguishedName': PRIMARY, 'objectSid': 'S-1-5-21-1-2-3-513', 'groupType': -2147483646}],
            [{'name': 'Empty', 'distinguishedName': OU}])


class MembershipWorkflowTests(TransactionTestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('member-admin', is_staff=True)
        self.client.force_login(self.admin)
        self.config = Domain_Controller_Config.objects.create(host='ldap.example.test', base_dn=BASE,
                         bind_username='reader', bind_password='test-only')
        apply_domain_snapshot(snapshot())
        self.account = Domain_Account.objects.get()
        self.computer = Domain_Computer.objects.get()
        self.group = Domain_Group.objects.get(distinguished_name=GROUP)

    def test_sync_populates_names_primary_and_empty_ou_and_replaces_removed_relations(self):
        self.assertIn('Domain Users（主组）', self.account.group_names)
        self.assertIn('Operators', self.account.group_names)
        self.assertEqual(self.computer.group_names, 'Operators')
        self.assertTrue(DomainOU.objects.filter(distinguished_name=OU, is_available=True).exists())
        data = snapshot()
        data[2][0]['member'] = [PC]
        apply_domain_snapshot(data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.group_names, 'Domain Users（主组）')
        self.assertEqual(DomainMembership.objects.count(), 2)

    def test_list_filter_and_export_use_the_same_full_group_scope(self):
        for route in ('domain_account_list', 'domain_computer_list'):
            response = self.client.get(reverse(route), {'filter_group_names': 'Operators'})
            self.assertContains(response, '所属分组')
            self.assertEqual(response.context['page_obj'].paginator.count, 1)
        response = self.client.get(reverse('table_export', args=['domain_accounts']), {'filter_group_names': 'Operators'})
        csv = b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertIn('所属分组', csv)
        self.assertIn('Operators', csv)

    def test_selectors_show_synced_empty_ou_and_group_with_dn(self):
        response = self.client.get(reverse('domain_account_list'))
        self.assertContains(response, 'value="' + OU + '"')
        self.assertContains(response, 'value="' + GROUP + '"')
        self.assertNotContains(response, 'type="text" id="domain-operation-group"')
        for action, field, value in [('add_group', 'group_dn', GROUP), ('move_ou', 'destination_dn', OU)]:
            with self.subTest(action=action):
                from index.domain.forms import DomainOperationForm
                data = {'object_type': 'account', 'action': action, 'target_ids': [str(self.account.pk)], field: value}
                form = DomainOperationForm(data, target_choices=[self.account.pk])
                self.assertTrue(form.is_valid(), form.errors)
                data[field] = 'CN=NotSynced,' + BASE
                self.assertFalse(DomainOperationForm(data, target_choices=[self.account.pk]).is_valid())

    def test_group_modal_enqueues_only_chosen_member_and_worker_updates_mirror(self):
        relation = DomainMembership.objects.get(group=self.group, account=self.account)
        url = reverse('domain_group_members', args=[self.group.pk])
        response = self.client.get(url)
        self.assertContains(response, 'PC01')
        response = self.client.post(url, {'membership_ids': [relation.pk]})
        self.assertContains(response, '任务已入队')
        task = TaskRun.objects.get(task_type='domain_operation')
        self.assertEqual(task.total_targets, 1)
        self.assertEqual(task.parameters_snapshot['group_dn'], GROUP)
        with patch('net.domain.executor.DomainClient.execute', return_value=DomainActionResult(True)) as execute:
            self.assertTrue(TaskWorker(worker_id='group-test', threads=1).run_once())
        self.assertEqual(execute.call_args.args[:3], ('account', 'remove_group', USER))
        self.assertFalse(DomainMembership.objects.filter(pk=relation.pk).exists())
        self.assertTrue(DomainMembership.objects.filter(group=self.group, computer=self.computer).exists())
        self.account.refresh_from_db()
        self.group.refresh_from_db()
        self.assertEqual(self.account.group_names, 'Domain Users（主组）')
        self.assertEqual(self.group.member_count, 1)

    def test_move_and_add_selectors_submit_to_worker_and_refresh_local_state(self):
        data = snapshot()
        data[2][0]['member'] = [PC]
        apply_domain_snapshot(data)
        url = reverse('domain_operation_create')
        for action, field, value, details in (
            ('add_group', 'group_dn', GROUP, {}),
            ('move_ou', 'destination_dn', OU, {'distinguished_name': 'CN=Alice,' + OU, 'ou': OU}),
        ):
            response = self.client.post(url, {'object_type': 'account', 'action': action,
                'target_ids': [self.account.pk], field: value})
            self.assertEqual(response.status_code, 302)
            self.assertIn('/tasks/', response.url)
            with patch('net.domain.executor.DomainClient.execute', return_value=DomainActionResult(True, details=details)):
                self.assertTrue(TaskWorker(worker_id='selector-test', threads=1).run_once())
        self.account.refresh_from_db()
        self.assertIn('Operators', self.account.group_names)
        self.assertEqual(self.account.ou, OU)

    def test_primary_cross_group_and_duplicate_post_rejected(self):
        primary = DomainMembership.objects.get(account=self.account, is_primary=True)
        url = reverse('domain_group_members', args=[self.group.pk])
        for ids in ([primary.pk], [999999], [primary.pk, primary.pk]):
            self.client.post(url, {'membership_ids': ids})
        self.assertFalse(TaskRun.objects.exists())
        with self.assertRaises(ValidationError):
            enqueue_domain_operation(requested_by=self.admin, object_type='account', action='remove_group',
                target_ids=[self.account.pk], parameters={'group_dn': PRIMARY})

    def test_remove_failure_keeps_relationship_and_repeated_enqueue_is_blocked(self):
        relation = DomainMembership.objects.get(group=self.group, account=self.account)
        url = reverse('domain_group_members', args=[self.group.pk])
        self.client.post(url, {'membership_ids': [relation.pk]})
        self.client.post(url, {'membership_ids': [relation.pk]})
        self.assertEqual(TaskRun.objects.count(), 1)
        with patch('net.domain.executor.DomainClient.execute', return_value=DomainActionResult(False, 'LDAP 权限不足。')):
            TaskWorker(worker_id='group-test', threads=1).run_once()
        self.assertTrue(DomainMembership.objects.filter(pk=relation.pk).exists())
        self.assertEqual(TaskRun.objects.get().status, 'failed')

    def test_permissions_and_no_get_mutation(self):
        url = reverse('domain_group_members', args=[self.group.pk])
        self.client.get(url)
        self.assertFalse(TaskRun.objects.exists())
        reader = get_user_model().objects.create_user('member-reader')
        self.client.force_login(reader)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.assertEqual(self.client.get(reverse('domain_account_list')).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(url, {}).status_code, 302)

    def test_sync_and_directory_mutations_do_not_overlap(self):
        from net.domain.sync_tasks import enqueue_domain_sync
        enqueue_domain_sync()
        with self.assertRaises(ValidationError):
            enqueue_domain_operation(requested_by=self.admin, object_type='account', action='remove_group',
                target_ids=[self.account.pk], parameters={'group_dn': GROUP})


class MembershipLdapTests(SimpleTestCase):
    def connection(self, group_sid='S-1-5-21-1-2-3-1200'):
        connection = MagicMock()
        responses = iter([{'objectSid': 'S-1-5-21-1-2-3-1000', 'primaryGroupID': 513}, {'objectSid': group_sid}])
        def search(*args, **kwargs):
            connection.response = [{'type': 'searchResEntry', 'attributes': next(responses)}]
            return True
        connection.search.side_effect = search
        connection.modify.return_value = True
        connection.result = {'result': 0}
        return connection

    def execute(self, connection):
        return DomainClient(SimpleNamespace(base_dn=BASE), connection_factory=lambda: connection).execute(
            'account', 'remove_group', USER, {'group_dn': GROUP})

    def test_ldap_removes_only_selected_member_and_closes_connection(self):
        connection = self.connection()
        self.assertTrue(self.execute(connection).success)
        connection.modify.assert_called_once_with(GROUP, {'member': [(MODIFY_DELETE, [USER])]}, controls=None)
        connection.unbind.assert_called_once()

    def test_live_primary_group_check_prevents_write(self):
        connection = self.connection('S-1-5-21-1-2-3-513')
        self.assertFalse(self.execute(connection).success)
        connection.modify.assert_not_called()

    def test_already_removed_is_success_only_after_membership_compare(self):
        connection = self.connection()
        def modify(*args, **kwargs):
            connection.result = {'result': 16}
            return False
        def compare(*args):
            connection.result = {'result': 5}
            return False
        connection.modify.side_effect = modify
        connection.compare.side_effect = compare
        self.assertTrue(self.execute(connection).success)


class DirectoryFetchTests(SimpleTestCase):
    def test_fetch_includes_empty_ous_and_primary_group_attributes(self):
        connection = MagicMock()
        data = snapshot()
        connection.result = {'result': 0}
        connection.extend.standard.paged_search.side_effect = [
            iter([{'type': 'searchResEntry', 'attributes': attrs} for attrs in rows]) for rows in data]
        with patch('net.domain.sync._connect', return_value=connection):
            result = fetch_domain_snapshot(SimpleNamespace(base_dn=BASE, user_filter='(objectClass=user)',
                computer_filter='(objectClass=computer)', group_filter='(objectClass=group)'))
        self.assertEqual(result[3], data[3])
        calls = connection.extend.standard.paged_search.call_args_list
        self.assertIn('primaryGroupID', calls[0].kwargs['attributes'])
        self.assertEqual(calls[3].kwargs['search_filter'], '(objectClass=organizationalUnit)')
        connection.unbind.assert_called_once()

    def test_member_ranges_are_completed_and_partial_failure_is_not_published(self):
        from net.domain.sync import _complete_group_members
        connection = MagicMock()
        connection.result = {'result': 0}
        connection.response = [{'type': 'searchResEntry', 'attributes': {'member;range=1-*': [PC]}}]
        attrs = {'distinguishedName': GROUP, 'member;range=0-0': USER}
        self.assertEqual(_complete_group_members(connection, attrs)['member'], [USER, PC])
        connection.search.return_value = False
        with self.assertRaises(RuntimeError):
            _complete_group_members(connection, attrs)
