"""Permission and public-output contracts for the domain-management UI."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from net.models import (
    DomainOperation,
    Domain_Account,
    Domain_Computer,
    Domain_Controller_Config,
    TaskRun,
)
from net.domain.tasks import enqueue_domain_operation


class DomainPermissionUiTests(TestCase):
    """The production break: removing the domain-operation permission gate."""

    def setUp(self):
        user_model = get_user_model()
        self.unprivileged_user = user_model.objects.create_user(
            username='domain-reader', password='test-password',
        )
        self.permitted_user = user_model.objects.create_user(
            username='domain-operator', password='test-password', is_staff=True,
        )
        self.superuser = user_model.objects.create_superuser(
            username='domain-admin', password='test-password', email='admin@example.test',
        )
        self.unprivileged_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='net', codename='manage_domain_operations',
            ),
        )
        self.computer = Domain_Computer.objects.create(
            computer_name='DOMAIN-PC-01',
            distinguished_name='CN=DOMAIN-PC-01,OU=Computers,DC=example,DC=test',
            is_active=True,
        )
        Domain_Controller_Config.objects.create(
            pk=1,
            name='Test Domain', host='ldap.example.test', port=389,
            base_dn='DC=example,DC=test', bind_username='svc-domain',
            bind_password='Never-Render-This-Bind-Password',
        )

    def _login_as(self, role):
        self.client.logout()
        if role is not None:
            self.client.force_login(role)

    def _assert_guest_login_redirect(self, response):
        from urllib.parse import urlencode
        self.assertRedirects(
            response,
            '/login/?' + urlencode({'next': response.request['PATH_INFO']}),
            fetch_redirect_response=False,
        )

    def test_four_roles_keep_domain_lists_read_only_and_hide_privileged_controls(self):
        """Making a list require permission would break inventory visibility."""
        cases = (
            ('anonymous', None, False),
            ('unprivileged', self.unprivileged_user, False),
            ('permitted', self.permitted_user, True),
            ('superuser', self.superuser, True),
        )

        for label, user, can_manage in cases:
            with self.subTest(role=label):
                self._login_as(user)
                settings_response = self.client.get(reverse('domain_controller_settings'))
                computer_response = self.client.get(reverse('domain_computer_list'))

                self.assertEqual(settings_response.status_code, 200 if can_manage else (302 if user is None else 403))
                self.assertNotIn('Never-Render-This-Bind-Password', settings_response.content.decode())
                if user is None:
                    self._assert_guest_login_redirect(settings_response)
                    self._assert_guest_login_redirect(computer_response)
                    continue
                self.assertEqual(computer_response.status_code, 200)
                self.assertEqual(
                    '域控连接设置' in settings_response.content.decode(), can_manage,
                )
                self.assertEqual(
                    'data-domain-operation-controls' in computer_response.content.decode(),
                    can_manage,
                )

    def test_domain_connection_password_uses_the_standard_input_style(self):
        """Replacing the write-only widget must not drop the shared form styling."""
        self.client.force_login(self.permitted_user)

        response = self.client.get(reverse('domain_controller_settings'))

        import re
        password_input = re.search(r'<input\b[^>]*name="bind_password"[^>]*>', response.content.decode()).group()
        self.assertIn('class="form-control"', password_input)
        self.assertIn('autocomplete="new-password"', password_input)

    @patch('index.domain.views.test_domain_connection', return_value='连接成功')
    @patch('net.domain.sync._connect', side_effect=AssertionError('Web sync must only enqueue'))
    def test_connection_mutations_require_domain_permission_for_all_four_roles(
        self, sync_domain, test_connection,
    ):
        """Removing the mutation guard would let a read-only caller contact LDAP."""
        payload = {
            'name': 'Updated Domain', 'host': 'ldap.example.test', 'port': 389,
            'base_dn': 'DC=example,DC=test', 'bind_username': 'svc-domain',
            'bind_password': 'Submitted-Bind-Password-Must-Not-Render',
            'user_filter': '(objectCategory=person)',
            'computer_filter': '(objectCategory=computer)',
            'group_filter': '(objectCategory=group)',
        }
        roles = (
            ('anonymous', None, 302),
            ('unprivileged', self.unprivileged_user, 403),
            ('permitted', self.permitted_user, 302),
            ('superuser', self.superuser, 302),
        )

        for action in ('save', 'test', 'sync'):
            for label, user, expected_status in roles:
                with self.subTest(action=action, role=label):
                    self._login_as(user)
                    response = self.client.post(
                        reverse('domain_controller_settings'), {**payload, 'action': action},
                    )
                    self.assertEqual(response.status_code, expected_status)
                    if user is None:
                        self._assert_guest_login_redirect(response)
                    self.assertNotIn(
                        'Submitted-Bind-Password-Must-Not-Render',
                        response.content.decode('utf-8', errors='replace'),
                    )

        self.assertEqual(sync_domain.call_count, 0)
        from net.models import TaskRun
        self.assertEqual(TaskRun.objects.filter(task_type='domain_sync').count(), 1)
        self.assertEqual(test_connection.call_count, 2)

    def test_operation_create_and_retry_require_domain_permission_for_all_four_roles(self):
        """Bypassing either endpoint would permit unreviewed directory writes."""
        create_url = '/domain/operations/create/'
        create_payload = {
            'object_type': 'computer', 'action': 'disable',
            'target_ids': [str(self.computer.pk)],
        }
        roles = (
            ('anonymous', None, 302),
            ('unprivileged', self.unprivileged_user, 403),
            ('permitted', self.permitted_user, 302),
            ('superuser', self.superuser, 302),
        )
        failed_operation = enqueue_domain_operation(
            requested_by=self.permitted_user,
            object_type=DomainOperation.ObjectType.COMPUTER,
            action=DomainOperation.Action.MOVE_OU,
            target_ids=[self.computer.pk],
            parameters={'destination_dn': 'OU=Managed,DC=example,DC=test'},
        )
        failed_at = timezone.now()
        failed_operation.task.target_runs.update(
            status=TaskRun.Status.FAILED,
            finished_at=failed_at,
            error_message='模拟目录操作失败。',
        )
        failed_operation.task.status = TaskRun.Status.FAILED
        failed_operation.task.finished_at = failed_at
        failed_operation.task.progress = 100
        failed_operation.task.completed_targets = 1
        failed_operation.task.successful_targets = 0
        failed_operation.task.failed_targets = 1
        failed_operation.task.save(update_fields=[
            'status', 'finished_at', 'progress', 'completed_targets',
            'successful_targets', 'failed_targets',
        ])
        failed_operation.status = DomainOperation.Status.FAILED
        failed_operation.finished_at = failed_at
        failed_operation.save(update_fields=['status', 'finished_at'])

        for label, user, expected_status in roles:
            with self.subTest(endpoint='create', role=label):
                self._login_as(user)
                payload = {**create_payload, 'action': 'disable' if label != 'superuser' else 'enable'}
                response = self.client.post(create_url, payload)
                self.assertEqual(response.status_code, expected_status)
                if user is None:
                    self._assert_guest_login_redirect(response)

        retry_url = f'/domain/operations/{failed_operation.pk}/retry/'
        for label, user, expected_status in roles:
            with self.subTest(endpoint='retry', role=label):
                self._login_as(user)
                response = self.client.post(retry_url)
                self.assertEqual(response.status_code, expected_status)
                if user is None:
                    self._assert_guest_login_redirect(response)

    def test_computer_operation_ui_offers_only_supported_non_password_actions(self):
        """Adding password controls to computers would create an unsupported AD request."""
        self.client.force_login(self.permitted_user)

        response = self.client.get(reverse('domain_computer_list'))

        self.assertEqual(response.status_code, 200)
        for action in ('move_ou', 'add_group', 'enable', 'disable'):
            self.assertContains(response, f'value="{action}"')
        self.assertNotContains(response, 'value="remove_group"')
        for action in ('create_user', 'reset_password', 'must_change_password', 'password_never_expires'):
            self.assertNotContains(response, f'value="{action}"')
        self.assertContains(response, str(self.computer.pk))

    def test_account_operation_ui_offers_account_password_and_expiry_actions(self):
        """Removing account-only controls would make valid AD actions unreachable."""
        account = Domain_Account.objects.create(
            account_name='Domain User', login_name='domain.user',
            distinguished_name='CN=Domain User,OU=Users,DC=example,DC=test',
        )
        self.client.force_login(self.permitted_user)

        response = self.client.get(reverse('domain_account_list'))

        self.assertEqual(response.status_code, 200)
        for action in ('reset_password', 'must_change_password', 'password_never_expires'):
            self.assertContains(response, f'value="{action}"')
        self.assertContains(response, str(account.pk))

    def test_account_operation_ui_offers_create_user_fields_without_existing_accounts(self):
        """Requiring a selected account would leave create_user without a usable entry point."""
        Domain_Account.objects.all().delete()
        self.client.force_login(self.permitted_user)

        response = self.client.get(reverse('domain_account_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="create_user"')
        for field_name in (
            'user_dn', 'login_name', 'display_name',
            'initial_password', 'initial_password_confirm',
        ):
            self.assertContains(response, f'name="{field_name}"')
        # 创建单个账号和批量导入各自提供一组密码与确认字段。
        self.assertContains(response, 'autocomplete="new-password"', count=4)

    @override_settings(
        DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=',
    )
    def test_create_user_submission_without_existing_accounts_keeps_four_role_permissions(self):
        """Only permitted staff and superusers may queue an unsynced create_user target."""
        Domain_Account.objects.all().delete()
        roles = (
            ('anonymous', None, False),
            ('unprivileged', self.unprivileged_user, False),
            ('permitted', self.permitted_user, True),
            ('superuser', self.superuser, True),
        )

        for index, (label, user, allowed) in enumerate(roles):
            with self.subTest(role=label):
                self._login_as(user)
                before = DomainOperation.objects.filter(action='create_user').count()
                response = self.client.post(reverse('domain_operation_create'), {
                    'object_type': 'account',
                    'action': 'create_user',
                    'user_dn': f'CN=New User {index},OU=Users,DC=example,DC=test',
                    'login_name': f'new.user.{index}',
                    'display_name': f'New User {index}',
                    'initial_password': 'Initial-Password-Only-In-Secret!',
                    'initial_password_confirm': 'Initial-Password-Only-In-Secret!',
                })

                self.assertEqual(response.status_code, 302 if allowed or user is None else 403)
                if user is None:
                    self._assert_guest_login_redirect(response)
                self.assertEqual(
                    DomainOperation.objects.filter(action='create_user').count(),
                    before + int(allowed),
                )
                self.assertNotIn(
                    'Initial-Password-Only-In-Secret!',
                    response.content.decode('utf-8', errors='replace'),
                )

    def test_invalid_operation_parameters_do_not_enqueue_or_echo_submitted_password(self):
        """Accepting incomplete action parameters would enqueue an unsafe directory write."""
        self.client.force_login(self.permitted_user)

        response = self.client.post('/domain/operations/create/', {
            'object_type': 'computer', 'action': 'move_ou',
            'target_ids': [str(self.computer.pk)],
            'password': 'Never-Echo-Operation-Password',
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(DomainOperation.objects.filter(action='move_ou').exists())
        self.assertNotIn(
            'Never-Echo-Operation-Password',
            response.content.decode('utf-8', errors='replace'),
        )

    def test_domain_task_detail_shows_redacted_action_targets_results_and_permitted_retry(self):
        """Dropping operation context would hide the audit evidence needed for retry decisions."""
        operation = enqueue_domain_operation(
            requested_by=self.permitted_user,
            object_type=DomainOperation.ObjectType.COMPUTER,
            action=DomainOperation.Action.MOVE_OU,
            target_ids=[self.computer.pk],
            parameters={'destination_dn': 'OU=Managed,DC=example,DC=test'},
        )
        completed_at = timezone.now()
        target = operation.task.target_runs.get()
        target.status = TaskRun.Status.FAILED
        target.finished_at = completed_at
        target.error_message = '目录服务器拒绝目标操作。'
        target.save()
        operation.task.status = TaskRun.Status.FAILED
        operation.task.finished_at = completed_at
        operation.task.progress = 100
        operation.task.completed_targets = 1
        operation.task.failed_targets = 1
        operation.task.save(update_fields=[
            'status', 'finished_at', 'progress', 'completed_targets', 'failed_targets',
        ])
        operation.status = DomainOperation.Status.FAILED
        operation.finished_at = completed_at
        operation.save(update_fields=['status', 'finished_at'])

        self.client.force_login(self.unprivileged_user)
        readonly = self.client.get(reverse('task_detail', args=[operation.task.pk]))
        self.client.force_login(self.permitted_user)
        permitted = self.client.get(reverse('task_detail', args=[operation.task.pk]))

        self.assertEqual(readonly.status_code, 200)
        self.assertContains(readonly, '移动到 OU')
        self.assertNotContains(readonly, 'data-domain-retry')
        self.assertContains(permitted, 'data-domain-operation-detail')
        self.assertContains(permitted, 'OU=Managed,DC=example,DC=test')
        self.assertContains(permitted, 'DOMAIN-PC-01')
        self.assertContains(permitted, '目录服务器拒绝目标操作。')
        self.assertContains(permitted, 'data-domain-retry')

    @override_settings(
        DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=',
    )
    def test_uncertain_create_user_hides_retry_and_requires_manual_directory_check(self):
        """An unknown add outcome must not advertise an automatic retry that could take over a user."""
        user_dn = 'CN=Manual Check,OU=Users,DC=example,DC=test'
        operation = enqueue_domain_operation(
            requested_by=self.permitted_user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.CREATE_USER,
            target_ids=[],
            parameters={
                'user_dn': user_dn,
                'login_name': 'manual.check',
                'display_name': 'Manual Check',
            },
            password='Manual-Check-Password-Must-Disappear!',
        )
        completed_at = timezone.now()
        target = operation.task.target_runs.get()
        target.status = TaskRun.Status.FAILED
        target.started_at = completed_at
        target.finished_at = completed_at
        target.error_message = '新增用户结果不确定，需要人工核查。'
        target.result_snapshot = {
            'action': 'create_user',
            'success': False,
            'stage': 'manual_intervention_required',
            'distinguished_name': user_dn,
        }
        target.save(update_fields=[
            'status', 'started_at', 'finished_at', 'error_message', 'result_snapshot',
        ])
        operation.task.status = TaskRun.Status.FAILED
        operation.task.started_at = completed_at
        operation.task.finished_at = completed_at
        operation.task.progress = 100
        operation.task.completed_targets = 1
        operation.task.failed_targets = 1
        operation.task.save(update_fields=[
            'status', 'started_at', 'finished_at', 'progress',
            'completed_targets', 'failed_targets',
        ])
        operation.status = DomainOperation.Status.FAILED
        operation.started_at = completed_at
        operation.finished_at = completed_at
        operation.save(update_fields=['status', 'started_at', 'finished_at'])

        self.client.force_login(self.permitted_user)
        detail = self.client.get(reverse('task_detail', args=[operation.task.pk]))
        retry = self.client.post(
            reverse('domain_operation_retry', args=[operation.pk]),
            {'password': 'Must-Not-Be-Stored-Or-Used!'},
            follow=True,
        )

        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'data-domain-manual-intervention')
        self.assertContains(detail, '请人工检查目录对象后再处理')
        self.assertNotContains(detail, 'data-domain-retry')
        self.assertContains(retry, '需要人工核查')
        self.assertEqual(DomainOperation.objects.filter(action='create_user').count(), 1)
        combined = detail.content.decode() + retry.content.decode()
        self.assertNotIn('Manual-Check-Password-Must-Disappear!', combined)
        self.assertNotIn('Must-Not-Be-Stored-Or-Used!', combined)
