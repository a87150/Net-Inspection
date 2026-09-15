from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from unittest.mock import patch
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from net.domain.sync import sync_domain
from net.models import (
    DomainOperation, Domain_Account, Domain_Computer, Domain_Controller_Config,
    Domain_Group,
)


class DomainGroupModelTests(TestCase):
    def test_domain_group_stores_synced_group_identity_and_classification(self):
        try:
            model = apps.get_model('net', 'Domain_Group')
        except LookupError:
            self.fail('Domain_Group model is not registered')

        group = model.objects.create(
            object_guid='c3bd67e8-bac0-4ec5-853f-a782785ad95d',
            distinguished_name='CN=IT Admins,OU=Groups,DC=example,DC=com',
            group_name='IT Admins',
            login_name='it-admins',
            description='IT administrators',
            ou='OU=Groups',
            group_scope='global',
            group_category='security',
            member_count=3,
        )

        self.assertEqual(str(group), 'IT Admins')
        self.assertEqual(group.group_scope, 'global')
        self.assertEqual(group.group_category, 'security')
        self.assertEqual(group.member_count, 3)

    def test_domain_controller_has_a_default_group_filter(self):
        config = Domain_Controller_Config()

        self.assertEqual(
            getattr(config, 'group_filter', None),
            '(objectCategory=group)',
        )


class DomainGroupSyncTests(TestCase):
    @patch('net.domain.sync._connect')
    def test_sync_imports_group_scope_category_and_member_count(self, connect_mock):
        connection = connect_mock.return_value
        connection.extend.standard.paged_search.side_effect = [
            iter([]),
            iter([]),
            iter([{'type': 'searchResEntry', 'attributes': {
                'name': ['IT Admins'],
                'sAMAccountName': ['it-admins'],
                'description': ['IT administrators'],
                'distinguishedName': ['CN=IT Admins,OU=Groups,DC=example,DC=com'],
                'objectGUID': ['c3bd67e8-bac0-4ec5-853f-a782785ad95d'],
                'groupType': ['-2147483646'],
                'member': [
                    'CN=Alice,OU=Users,DC=example,DC=com',
                    'CN=Bob,OU=Users,DC=example,DC=com',
                ],
            }}]),
            iter([]),  # organizational units, including empty OUs
        ]
        config = Domain_Controller_Config(
            host='dc.example.com', base_dn='DC=example,DC=com',
            bind_username='EXAMPLE\\sync', bind_password='password',
        )

        counts = sync_domain(config)

        self.assertEqual(counts, (0, 0, 1))
        group = Domain_Group.objects.get(login_name='it-admins')
        self.assertEqual(group.group_scope, Domain_Group.Scope.GLOBAL)
        self.assertEqual(group.group_category, Domain_Group.Category.SECURITY)
        self.assertEqual(group.member_count, 2)
        self.assertEqual(group.ou, 'OU=Groups,DC=example,DC=com')
        connection.unbind.assert_called_once()


class DomainGroupWorkspaceTests(TestCase):
    def setUp(self):
        from tests.auth import login_admin
        login_admin(self.client)
        Domain_Account.objects.create(account_name='启用账号', login_name='active', is_active=True)
        Domain_Account.objects.create(account_name='停用账号', login_name='inactive', is_active=False)
        Domain_Computer.objects.create(computer_name='ACTIVE-PC', is_active=True)
        Domain_Computer.objects.create(computer_name='INACTIVE-PC', is_active=False)
        self.group = Domain_Group.objects.create(
            distinguished_name='CN=IT Admins,OU=Groups,DC=example,DC=com',
            group_name='IT Admins', login_name='it-admins',
            group_scope=Domain_Group.Scope.GLOBAL,
            group_category=Domain_Group.Category.SECURITY,
            member_count=2,
        )

    def test_overview_separates_enabled_disabled_counts_and_links_groups(self):
        response = self.client.get(reverse('domain_controller_settings'))

        self.assertContains(response, '域账号')
        self.assertContains(response, '启用')
        self.assertContains(response, '停用 1')
        self.assertContains(
            response,
            '<span class="text-success">1</span>',
            count=2,
            html=True,
        )
        self.assertContains(response, '域分组')
        self.assertContains(response, '/domain/groups/')
        self.assertContains(response, '安全组 1')
        self.assertContains(response, '通讯组 0')

    def test_group_list_uses_registered_table_filtering_and_details(self):
        response = self.client.get('/domain/groups/', {'filter_group_scope': 'global'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['table_definition'].key, 'domain_groups')
        self.assertContains(response, self.group.group_name)
        self.assertContains(response, f'/domain/groups/{self.group.pk}/')


class DomainGroupConfigTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(
            username='domain-group-admin', password='unused',
        )
        self.client.force_login(user)

    def test_group_filter_is_visible_and_saved_with_domain_settings(self):
        listing = self.client.get(reverse('domain_controller_settings'))
        self.assertContains(listing, '分组过滤器')

        response = self.client.post(reverse('domain_controller_settings'), {
            'name': '公司域控', 'host': 'dc.example.com', 'port': 389,
            'base_dn': 'DC=example,DC=com', 'bind_username': 'EXAMPLE\\sync',
            'bind_password': 'password',
            'user_filter': '(&(objectCategory=person)(objectClass=user))',
            'computer_filter': '(objectCategory=computer)',
            'group_filter': '(&(objectCategory=group)(cn=IT*))',
            'action': 'save',
        })

        self.assertRedirects(response, reverse('domain_controller_settings') + '?modal=1')
        self.assertEqual(
            Domain_Controller_Config.objects.get().group_filter,
            '(&(objectCategory=group)(cn=IT*))',
        )


@override_settings(
    DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=',
)
class DomainAccountTableImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='domain-import-admin', password='unused',
        )
        self.client.force_login(self.user)
        Domain_Controller_Config.objects.create(
            host='dc.example.com', base_dn='DC=example,DC=com',
            bind_username='EXAMPLE\\sync', bind_password='password',
        )

    def _post_csv(self, text):
        return self.client.post('/domain/accounts/import/', {
            'file': SimpleUploadedFile(
                'accounts.csv', text.encode('utf-8-sig'), content_type='text/csv',
            ),
            'initial_password': 'Temporary-Passw0rd!',
            'initial_password_confirm': 'Temporary-Passw0rd!',
        })

    def _post_xlsx(self):
        strings = (
            '用户DN', '登录名', '显示名称',
            'CN=Carol,OU=Users,DC=example,DC=com', 'carol', '卡洛尔',
        )
        shared = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + ''.join(f'<si><t>{value}</t></si>' for value in strings)
            + '</sst>'
        )
        sheet = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2" t="s"><v>4</v></c><c r="C2" t="s"><v>5</v></c></row></sheetData></worksheet>'
        )
        output = BytesIO()
        with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('xl/sharedStrings.xml', shared)
            archive.writestr('xl/worksheets/sheet1.xml', sheet)
        return self.client.post('/domain/accounts/import/', {
            'file': SimpleUploadedFile(
                'accounts.xlsx', output.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            ),
            'initial_password': 'Temporary-Passw0rd!',
            'initial_password_confirm': 'Temporary-Passw0rd!',
        })

    def test_account_list_offers_csv_and_xlsx_batch_import(self):
        response = self.client.get(reverse('domain_account_list'))

        self.assertContains(response, '批量导入账号')
        self.assertContains(response, 'accept=".csv,.xlsx"')
        self.assertContains(response, '下载 CSV 模板')
        self.assertContains(response, '下载 Excel 模板')

    def test_csv_and_excel_templates_are_downloadable(self):
        csv_response = self.client.get('/domain/accounts/import/template/csv/')
        excel_response = self.client.get('/domain/accounts/import/template/xlsx/')

        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('用户DN,登录名,显示名称', csv_response.content.decode('utf-8-sig'))
        self.assertEqual(excel_response.status_code, 200)
        self.assertTrue(excel_response.content.startswith(b'PK'))
        self.assertEqual(
            excel_response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def test_valid_csv_creates_one_secret_safe_task_per_account(self):
        response = self._post_csv(
            '用户DN,登录名,显示名称\n'
            '"CN=Alice,OU=Users,DC=example,DC=com",alice,爱丽丝\n'
            '"CN=Bob,OU=Users,DC=example,DC=com",bob,鲍勃\n'
        )

        self.assertRedirects(response, reverse('task_list'))
        operations = list(DomainOperation.objects.filter(action='create_user'))
        self.assertEqual(len(operations), 2)
        self.assertTrue(all(operation.target_count == 1 for operation in operations))
        self.assertTrue(all(hasattr(operation, 'secret') for operation in operations))
        self.assertFalse(any(
            'password' in str(operation.parameter_summary).lower()
            for operation in operations
        ))

    def test_valid_excel_creates_an_account_task(self):
        response = self._post_xlsx()

        self.assertRedirects(response, reverse('task_list'))
        operation = DomainOperation.objects.get(action='create_user')
        self.assertEqual(operation.parameter_summary['login_name'], 'carol')
        self.assertTrue(hasattr(operation, 'secret'))

    def test_one_invalid_csv_row_rolls_back_the_whole_batch(self):
        response = self._post_csv(
            '用户DN,登录名,显示名称\n'
            '"CN=Alice,OU=Users,DC=example,DC=com",alice,爱丽丝\n'
            '"CN=Outside,OU=Users,DC=outside,DC=com",outside,域外用户\n'
        )

        self.assertRedirects(response, reverse('domain_account_list') + '?domain_modal=account_import')
        self.assertEqual(DomainOperation.objects.count(), 0)
