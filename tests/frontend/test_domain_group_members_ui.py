from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from net.models import (
    Domain_Account,
    Domain_Controller_Config,
    Domain_Group,
    DomainMembership,
)


class DomainGroupMemberInterfaceTests(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_user(
            'domain-member-ui-admin',
            is_staff=True,
        )
        self.client.force_login(admin)
        Domain_Controller_Config.objects.create(
            host='ldap.example.test',
            base_dn='DC=example,DC=test',
        )
        self.group = Domain_Group.objects.create(
            group_name='运维管理员',
            login_name='ops-admins',
            distinguished_name='CN=运维管理员,OU=Groups,DC=example,DC=test',
            member_count=1,
        )
        account = Domain_Account.objects.create(
            account_name='张三',
            login_name='zhangsan',
            distinguished_name='CN=张三,OU=People,DC=example,DC=test',
        )
        DomainMembership.objects.create(group=self.group, account=account)

    def test_member_modal_exposes_one_workspace_with_accessible_controls(self):
        response = self.client.get(reverse('domain_group_members', args=[self.group.pk]))

        self.assertContains(
            response,
            'class="modal-body modal-body--scroll domain-members-workspace"',
        )
        self.assertContains(response, 'class="domain-members-mode" role="tablist"')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, 'data-member-selection-summary')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'data-member-select="invert"')
        self.assertContains(response, 'class="table data-table table-hover')
        self.assertContains(response, 'form="domain-group-member-action"')
        self.assertContains(
            response,
            'class="modal-footer modal-footer--sticky domain-members-footer"',
        )

    def test_group_list_loading_dialog_uses_the_same_modal_shell(self):
        response = self.client.get(reverse('domain_group_list'))

        self.assertContains(response, 'id="domainGroupMembersModal"')
        self.assertContains(
            response,
            'class="modal-dialog modal-xl modal-dialog-scrollable modal-shell"',
        )
        self.assertContains(response, 'class="modal-content modal-surface"')
        self.assertContains(response, 'class="modal-loading-state"')
