import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from net import models
from net.domain.sync import sync_domain


class DomainOperationModelTests(SimpleTestCase):
    def test_domain_operation_is_available_from_the_public_models_api(self):
        """Removing the audited operation model must break domain-operation callers."""
        self.assertTrue(hasattr(models, 'DomainOperation'))

    def test_domain_operation_declares_the_audit_contract_and_approved_actions(self):
        """Dropping an audit field or allowing a cross-object action must be observable."""
        operation = models.DomainOperation

        self.assertTrue({
            'id', 'action', 'object_type', 'requested_by', 'status', 'target_count',
            'parameter_summary', 'task', 'started_at', 'finished_at', 'created_at',
            'updated_at',
        } <= {field.name for field in operation._meta.fields})
        self.assertEqual(
            {value for value, _label in operation.ObjectType.choices},
            {'account', 'computer'},
        )
        self.assertEqual(
            {value for value, _label in operation.Action.choices},
            {
                'create_user', 'move_ou', 'add_group', 'remove_group', 'move_group',
                'reset_password', 'must_change_password',
                'password_never_expires', 'unlock', 'enable', 'disable',
            },
        )
        self.assertEqual(
            {value for value, _label in operation.Status.choices},
            {'queued', 'running', 'success', 'partial', 'failed', 'cancelled'},
        )
        self.assertIn(
            ('manage_domain_operations', 'Can manage domain operations'),
            operation._meta.permissions,
        )

    def test_domain_objects_keep_stable_guid_and_distinguished_name_identity(self):
        """A later rename must not remove the GUID/DN used to resolve an operation."""
        for model in (models.Domain_Account, models.Domain_Computer):
            with self.subTest(model=model.__name__):
                guid = model._meta.get_field('object_guid')
                distinguished_name = model._meta.get_field('distinguished_name')
                self.assertTrue(guid.null)
                self.assertTrue(guid.blank)
                self.assertTrue(guid.unique)
                self.assertTrue(distinguished_name.null)
                self.assertTrue(distinguished_name.blank)
                self.assertTrue(distinguished_name.db_index)

    def test_task_models_offer_domain_operation_task_and_target_types(self):
        """Removing these task types would prevent domain operations entering the queue."""
        self.assertIn('domain_operation', models.TaskRun.TaskType.values)
        self.assertTrue({
            'domain_account', 'domain_computer',
        } <= set(models.TaskTargetRun.TargetType.values))

    def test_domain_target_exposes_an_indexed_execution_scope_slot(self):
        """Removing the database scope slot would reopen cross-process LDAP races."""
        fields = {
            field.name: field
            for field in models.TaskTargetRun._meta.fields
        }

        self.assertIn('execution_scope_key', fields)
        scope = fields['execution_scope_key']
        self.assertTrue(scope.null)
        self.assertTrue(scope.db_index)
        self.assertEqual(scope.max_length, 64)

    def test_domain_target_declares_a_unique_active_execution_scope(self):
        """Two queued targets for one canonical DN must be rejected by the database."""
        constraints = {
            constraint.name: constraint
            for constraint in models.TaskTargetRun._meta.constraints
        }

        constraint = constraints.get('net_target_active_execution_scope_uniq')
        self.assertIsNotNone(constraint)
        self.assertEqual(tuple(constraint.fields), ('execution_scope_key',))

    def test_domain_secret_is_registered_privately_with_one_time_payload_fields(self):
        """Exposing or removing the private secret envelope must break this contract."""
        secret_models = [
            model for model in apps.get_app_config('net').get_models()
            if model.__name__ == 'DomainOperationSecret'
        ]
        self.assertEqual(len(secret_models), 1)
        secret = secret_models[0]
        self.assertFalse(hasattr(models, 'DomainOperationSecret'))
        self.assertTrue({
            'operation', 'encrypted_payload', 'purpose_fingerprint', 'created_at',
            'expires_at',
        } <= {field.name for field in secret._meta.fields})
        self.assertEqual(secret._meta.get_field('encrypted_payload').get_internal_type(), 'BinaryField')
        self.assertTrue(secret._meta.get_field('operation').unique)
        self.assertEqual(secret._meta.get_field('purpose_fingerprint').max_length, 64)


class DomainOperationPersistenceTests(TestCase):
    SENSITIVE_FIELD_NAMES = (
        'password', 'newPassword', 'bindPassword', 'unicodePwd', 'userPassword',
        'initialPassword', 'ciphertext', 'encryptedPayload', 'clientSecret',
        'accessToken',
    )

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='domain-operator',
            password='not-a-domain-password',
        )

    def _operation(self, **overrides):
        values = {
            'action': models.DomainOperation.Action.DISABLE,
            'object_type': models.DomainOperation.ObjectType.ACCOUNT,
            'requested_by': self.user,
            'parameter_summary': {'reason': '离职'},
            'target_count': 1,
        }
        values.update(overrides)
        return models.DomainOperation.objects.create(**values)

    def _nested_sensitive_payload(self, key, value):
        return {'outer': [{'inner': ({key: value},)}]}

    def _domain_task(self, parameters_snapshot):
        target_id = str(uuid.uuid4())
        return models.TaskRun(
            task_type=models.TaskRun.TaskType.DOMAIN_OPERATION,
            source=models.TaskRun.Source.MANUAL,
            profile_snapshot={},
            parameters_snapshot=parameters_snapshot,
            selected_items_snapshot=[],
            target_scope_snapshot={'targets': [{
                'target_type': models.TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
                'target_id': target_id,
                'distinguished_name': 'CN=Zhang San,OU=Users,DC=example,DC=test',
            }]},
            total_targets=1,
        )

    def _saved_domain_task(self):
        task = self._domain_task({'action': 'disable'})
        task.save()
        return task

    def _assert_rejected_without_value(self, writer, value):
        with self.assertRaises(ValidationError) as raised:
            writer()
        self.assertNotIn(value, str(raised.exception))

    def test_domain_operation_has_custom_permission_and_non_sensitive_summary(self):
        """Removing the permission or accepting password-bearing summaries is unsafe."""
        operation = self._operation()
        permission = Permission.objects.get(codename='manage_domain_operations')

        self.assertEqual(permission.content_type.app_label, 'net')
        self.assertNotIn('password', json.dumps(operation.parameter_summary).lower())
        for key in ('password', 'new_password', 'initial_password', 'bind_password'):
            with self.subTest(key=key):
                unsafe = models.DomainOperation(
                    action=models.DomainOperation.Action.RESET_PASSWORD,
                    object_type=models.DomainOperation.ObjectType.ACCOUNT,
                    requested_by=self.user,
                    parameter_summary={key: 'never-store-this'},
                    target_count=1,
                )
                with self.assertRaises(ValidationError):
                    unsafe.full_clean()

    def test_domain_operation_keeps_audit_fields_immutable_after_creation(self):
        """Changing the action after enqueue would falsify the audit record."""
        operation = self._operation()
        operation.action = models.DomainOperation.Action.ENABLE

        with self.assertRaises(ValidationError):
            operation.save()

    def test_direct_domain_operation_writes_reject_nested_sensitive_ldap_fields(self):
        """Removing save-time validation would persist a plaintext operation summary."""
        for key in self.SENSITIVE_FIELD_NAMES:
            for write_kind in ('save', 'objects.create'):
                with self.subTest(key=key, write_kind=write_kind):
                    value = f'plaintext-{key}'
                    fields = {
                        'action': models.DomainOperation.Action.RESET_PASSWORD,
                        'object_type': models.DomainOperation.ObjectType.ACCOUNT,
                        'requested_by': self.user,
                        'parameter_summary': self._nested_sensitive_payload(key, value),
                        'target_count': 1,
                    }
                    if write_kind == 'save':
                        writer = lambda: models.DomainOperation(**fields).save()
                    else:
                        writer = lambda: models.DomainOperation.objects.create(**fields)
                    self._assert_rejected_without_value(writer, value)

    def test_direct_domain_task_and_target_writes_reject_sensitive_snapshots(self):
        """Bypassing full_clean must not persist secret-bearing domain task snapshots."""
        for key in self.SENSITIVE_FIELD_NAMES:
            for write_kind in ('save', 'objects.create'):
                with self.subTest(model='task', key=key, write_kind=write_kind):
                    value = f'plaintext-{key}'
                    task = self._domain_task(self._nested_sensitive_payload(key, value))
                    if write_kind == 'save':
                        writer = task.save
                    else:
                        writer = lambda: models.TaskRun.objects.create(
                            task_type=task.task_type,
                            source=task.source,
                            profile_snapshot=task.profile_snapshot,
                            parameters_snapshot=task.parameters_snapshot,
                            selected_items_snapshot=task.selected_items_snapshot,
                            target_scope_snapshot=task.target_scope_snapshot,
                            total_targets=task.total_targets,
                        )
                    self._assert_rejected_without_value(writer, value)

                with self.subTest(model='target', key=key, write_kind=write_kind):
                    value = f'plaintext-target-{key}'
                    task = self._saved_domain_task()
                    fields = {
                        'task': task,
                        'target_type': models.TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
                        'target_id': str(uuid.uuid4()),
                        'target_snapshot': self._nested_sensitive_payload(key, value),
                    }
                    target = models.TaskTargetRun(**fields)
                    if write_kind == 'save':
                        writer = target.save
                    else:
                        writer = lambda: models.TaskTargetRun.objects.create(**fields)
                    self._assert_rejected_without_value(writer, value)

    def test_direct_domain_target_result_snapshot_write_rejects_sensitive_payload(self):
        """A Worker result snapshot is also a normal persistence path and must be redacted."""
        for write_kind in ('save', 'objects.create'):
            with self.subTest(write_kind=write_kind):
                value = 'plaintext-result-newPassword'
                task = self._saved_domain_task()
                fields = {
                    'task': task,
                    'target_type': models.TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
                    'target_id': str(uuid.uuid4()),
                    'target_snapshot': {'id': str(uuid.uuid4())},
                    'result_snapshot': self._nested_sensitive_payload('newPassword', value),
                }
                target = models.TaskTargetRun(**fields)
                if write_kind == 'save':
                    writer = target.save
                else:
                    writer = lambda: models.TaskTargetRun.objects.create(**fields)
                self._assert_rejected_without_value(writer, value)

    def test_non_domain_task_and_target_saves_remain_compatible(self):
        """Applying the domain guard to existing task types would reject legacy snapshots."""
        profile = models.InspectionProfile.objects.create(
            name='legacy-snapshot-profile',
            device_type=models.InspectionProfile.DeviceType.NETWORK_DEVICE,
            selected_items=['cpu'],
            target_selector={},
        )
        task = models.TaskRun(
            task_type=models.TaskRun.TaskType.INSPECTION,
            source=models.TaskRun.Source.MANUAL,
            inspection_profile=profile,
            profile_snapshot={'device_type': 'network_device'},
            parameters_snapshot={'newPassword': 'legacy-value'},
            selected_items_snapshot=['cpu'],
            target_scope_snapshot={'targets': [{
                'target_type': models.TaskTargetRun.TargetType.NETWORK_DEVICE,
                'target_id': str(uuid.uuid4()),
            }]},
            total_targets=1,
        )
        task.save()
        target = models.TaskTargetRun.objects.create(
            task=task,
            target_type=models.TaskTargetRun.TargetType.NETWORK_DEVICE,
            target_id=str(uuid.uuid4()),
            target_snapshot={'newPassword': 'legacy-value'},
        )

        self.assertTrue(models.TaskRun.objects.filter(pk=task.pk).exists())
        self.assertTrue(models.TaskTargetRun.objects.filter(pk=target.pk).exists())

    def test_domain_operation_secret_is_one_to_one_and_private(self):
        """A second payload for one operation would break one-time consumption."""
        secret_model = apps.get_model('net', 'DomainOperationSecret')
        operation = self._operation()
        secret_model.objects.create(
            operation=operation,
            encrypted_payload=b'ciphertext',
            purpose_fingerprint='a' * 64,
            expires_at='2026-09-02T00:00:00+08:00',
        )

        with self.assertRaises(IntegrityError):
            secret_model.objects.create(
                operation=operation,
                encrypted_payload=b'other-ciphertext',
                purpose_fingerprint='b' * 64,
                expires_at='2026-09-02T00:00:00+08:00',
            )
        self.assertFalse(hasattr(models, 'DomainOperationSecret'))

    def test_domain_task_accepts_only_non_sensitive_domain_snapshots(self):
        """A password key in an immutable task snapshot must be rejected before save."""
        scope = {
            'targets': [{
                'target_type': models.TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
                'target_id': str(uuid.uuid4()),
                'distinguished_name': 'CN=Zhang San,OU=Users,DC=example,DC=test',
            }],
        }
        task = models.TaskRun(
            task_type=models.TaskRun.TaskType.DOMAIN_OPERATION,
            source=models.TaskRun.Source.MANUAL,
            profile_snapshot={},
            parameters_snapshot={'action': 'disable'},
            selected_items_snapshot=[],
            target_scope_snapshot=scope,
            scope_key='a' * 64,
            active_scope_key='a' * 64,
            total_targets=1,
        )
        task.full_clean()
        target = models.TaskTargetRun(
            task=task,
            target_type=models.TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
            target_id=scope['targets'][0]['target_id'],
            target_snapshot={
                'id': scope['targets'][0]['target_id'],
                'distinguished_name': scope['targets'][0]['distinguished_name'],
            },
        )
        target.clean()

        for key in ('password', 'new_password', 'initial_password', 'bind_password'):
            with self.subTest(key=key):
                task.parameters_snapshot = {key: 'never-store-this'}
                with self.assertRaises(ValidationError):
                    task.full_clean()

        target.target_snapshot['encrypted_payload'] = 'never-store-this'
        with self.assertRaises(ValidationError):
            target.clean()

    def test_database_releases_a_domain_execution_scope_after_the_target_ends(self):
        """Dropping the partial uniqueness would allow concurrent LDAP writes to one DN."""
        first_task = self._saved_domain_task()
        second_task = self._saved_domain_task()
        scope = 'a' * 64
        first = models.TaskTargetRun.objects.create(
            task=first_task,
            target_type=models.TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
            target_id=str(uuid.uuid4()),
            target_snapshot={
                'id': str(uuid.uuid4()),
                'distinguished_name': 'CN=First,OU=Users,DC=example,DC=test',
            },
            execution_scope_key=scope,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                models.TaskTargetRun.objects.create(
                    task=second_task,
                    target_type=models.TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
                    target_id=str(uuid.uuid4()),
                    target_snapshot={
                        'id': str(uuid.uuid4()),
                        'distinguished_name': 'CN=Second,OU=Users,DC=example,DC=test',
                    },
                    execution_scope_key=scope,
                )

        first.status = models.TaskRun.Status.FAILED
        first.finished_at = timezone.now()
        first.save(update_fields={'status', 'finished_at'})
        replacement = models.TaskTargetRun.objects.create(
            task=second_task,
            target_type=models.TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
            target_id=str(uuid.uuid4()),
            target_snapshot={
                'id': str(uuid.uuid4()),
                'distinguished_name': 'CN=Second,OU=Users,DC=example,DC=test',
            },
            execution_scope_key=scope,
        )

        self.assertEqual(replacement.execution_scope_key, scope)

    @patch('net.domain.sync._connect')
    def test_domain_sync_persists_guid_and_dn_without_contacting_ldap(self, connect_mock):
        """Changing a login/name must update the existing GUID-matched object, not duplicate it."""
        account_guid = uuid.uuid4()
        computer_guid = uuid.uuid4()
        account = models.Domain_Account.objects.create(
            account_name='旧姓名',
            login_name='old.zhang',
            object_guid=account_guid,
            distinguished_name='CN=Old Zhang,OU=Users,DC=example,DC=test',
        )
        computer = models.Domain_Computer.objects.create(
            computer_name='OLD-PC',
            object_guid=computer_guid,
            distinguished_name='CN=OLD-PC,OU=Computers,DC=example,DC=test',
        )
        connection = connect_mock.return_value
        connection.extend.standard.paged_search.side_effect = [
            iter([{
                'type': 'searchResEntry',
                'attributes': {
                    'displayName': '张三', 'sAMAccountName': 'zhang.san',
                    'userAccountControl': 0, 'distinguishedName': 'CN=Zhang San,OU=Users,DC=example,DC=test',
                    'objectGUID': str(account_guid), 'lastLogonTimestamp': 0,
                    'userWorkstations': '',
                },
            }]),
            iter([{
                'type': 'searchResEntry',
                'attributes': {
                    'name': 'PC-001', 'operatingSystem': 'Windows 11',
                    'userAccountControl': 0, 'distinguishedName': 'CN=PC-001,OU=Computers,DC=example,DC=test',
                    'objectGUID': str(computer_guid), 'lastLogonTimestamp': 0,
                },
            }]),
            iter([]),
            iter([]),  # organizational units, including empty OUs
        ]
        config = SimpleNamespace(
            base_dn='DC=example,DC=test',
            user_filter='(objectCategory=user)',
            computer_filter='(objectCategory=computer)',
            group_filter='(objectCategory=group)',
        )

        self.assertEqual(sync_domain(config), (1, 1, 0))
        account.refresh_from_db()
        computer.refresh_from_db()
        self.assertEqual(models.Domain_Account.objects.count(), 1)
        self.assertEqual(models.Domain_Computer.objects.count(), 1)
        self.assertEqual(account.login_name, 'zhang.san')
        self.assertEqual(account.distinguished_name, 'CN=Zhang San,OU=Users,DC=example,DC=test')
        self.assertEqual(computer.computer_name, 'PC-001')
        self.assertEqual(computer.distinguished_name, 'CN=PC-001,OU=Computers,DC=example,DC=test')
        connection.unbind.assert_called_once_with()

    @patch('net.domain.sync._connect')
    def test_domain_sync_preserves_existing_identity_when_ldap_values_are_missing_or_invalid(self, connect_mock):
        """Writing a missing or malformed LDAP identity must not erase a stable local identity."""
        account_guid = uuid.uuid4()
        computer_guid = uuid.uuid4()
        account = models.Domain_Account.objects.create(
            account_name='张三', login_name='zhang.san', object_guid=account_guid,
            distinguished_name='CN=Zhang San,OU=Users,DC=example,DC=test',
        )
        computer = models.Domain_Computer.objects.create(
            computer_name='PC-001', object_guid=computer_guid,
            distinguished_name='CN=PC-001,OU=Computers,DC=example,DC=test',
        )
        connection = connect_mock.return_value
        connection.extend.standard.paged_search.side_effect = [
            iter([{'type': 'searchResEntry', 'attributes': {
                'displayName': '张三', 'sAMAccountName': 'zhang.san',
                'userAccountControl': 0, 'lastLogonTimestamp': 0,
                'userWorkstations': '',
            }}]),
            iter([{'type': 'searchResEntry', 'attributes': {
                'name': 'PC-001', 'operatingSystem': 'Windows 11',
                'userAccountControl': 0, 'objectGUID': 'not-a-guid',
                'distinguishedName': 'not-a-dn', 'lastLogonTimestamp': 0,
            }}]),
            iter([]),
            iter([]),  # organizational units, including empty OUs
        ]
        config = SimpleNamespace(
            base_dn='DC=example,DC=test', user_filter='(objectCategory=user)',
            computer_filter='(objectCategory=computer)',
            group_filter='(objectCategory=group)',
        )

        self.assertEqual(sync_domain(config), (1, 1, 0))
        account.refresh_from_db()
        computer.refresh_from_db()
        self.assertEqual(account.object_guid, account_guid)
        self.assertEqual(account.distinguished_name, 'CN=Zhang San,OU=Users,DC=example,DC=test')
        self.assertEqual(computer.object_guid, computer_guid)
        self.assertEqual(computer.distinguished_name, 'CN=PC-001,OU=Computers,DC=example,DC=test')

    @patch('net.domain.sync._connect')
    def test_domain_sync_guid_identity_conflict_preserves_both_local_rows(self, connect_mock):
        """A GUID match must not overwrite a different local row's unique login/name."""
        account_guid = uuid.uuid4()
        computer_guid = uuid.uuid4()
        account = models.Domain_Account.objects.create(
            account_name='GUID account', login_name='guid.account', object_guid=account_guid,
        )
        account_conflict = models.Domain_Account.objects.create(
            account_name='local conflict', login_name='incoming.account', object_guid=uuid.uuid4(),
        )
        computer = models.Domain_Computer.objects.create(
            computer_name='GUID-PC', object_guid=computer_guid,
        )
        computer_conflict = models.Domain_Computer.objects.create(
            computer_name='INCOMING-PC', object_guid=uuid.uuid4(),
        )
        connection = connect_mock.return_value
        connection.extend.standard.paged_search.side_effect = [
            iter([{'type': 'searchResEntry', 'attributes': {
                'displayName': 'GUID account', 'sAMAccountName': 'incoming.account',
                'userAccountControl': 0, 'objectGUID': str(account_guid),
                'distinguishedName': 'CN=GUID Account,OU=Users,DC=example,DC=test',
                'lastLogonTimestamp': 0, 'userWorkstations': '',
            }}]),
            iter([{'type': 'searchResEntry', 'attributes': {
                'name': 'INCOMING-PC', 'operatingSystem': 'Windows 11',
                'userAccountControl': 0, 'objectGUID': str(computer_guid),
                'distinguishedName': 'CN=GUID-PC,OU=Computers,DC=example,DC=test',
                'lastLogonTimestamp': 0,
            }}]),
            iter([]),
            iter([]),  # organizational units, including empty OUs
        ]
        config = SimpleNamespace(
            base_dn='DC=example,DC=test', user_filter='(objectCategory=user)',
            computer_filter='(objectCategory=computer)',
            group_filter='(objectCategory=group)',
        )

        self.assertEqual(sync_domain(config), (1, 1, 0))
        account.refresh_from_db()
        account_conflict.refresh_from_db()
        computer.refresh_from_db()
        computer_conflict.refresh_from_db()
        self.assertEqual(account.login_name, 'guid.account')
        self.assertEqual(account_conflict.login_name, 'incoming.account')
        self.assertEqual(computer.computer_name, 'GUID-PC')
        self.assertEqual(computer_conflict.computer_name, 'INCOMING-PC')
        self.assertTrue(account.is_active)
        self.assertTrue(account_conflict.is_active)
        self.assertTrue(computer.is_active)
        self.assertTrue(computer_conflict.is_active)
