"""TDD coverage for queued, lease-fenced domain operations.

All directory interactions are replaced with a local fake; these tests must
never make a real LDAP connection.
"""

import json
import threading
import time
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django.db import IntegrityError, close_old_connections, connections, transaction

from net.models import (
    DomainOperation, Domain_Account, Domain_Computer, Domain_Controller_Config, TaskRun,
    TaskTargetRun,
)


class DomainOperationQueueTests(TestCase):
    """The service snapshots stable identifiers, never submitted secrets."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='domain-operator', password='local-test-password',
        )
        self.first = Domain_Account.objects.create(
            account_name='First Account',
            login_name='first.account',
            distinguished_name='CN=First Account,OU=Users,DC=example,DC=test',
        )
        self.second = Domain_Account.objects.create(
            account_name='Second Account',
            login_name='second.account',
            distinguished_name='CN=Second Account,OU=Users,DC=example,DC=test',
        )
        self.third = Domain_Account.objects.create(
            account_name='Third Account',
            login_name='third.account',
            distinguished_name='CN=Third Account,OU=Users,DC=example,DC=test',
        )
        Domain_Controller_Config.objects.create(
            host='dc.example.test', base_dn='DC=example,DC=test',
            bind_username='queue', bind_password='not-a-real-directory-secret',
        )

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_enqueue_snapshots_stable_target_identity_without_password(self):
        """Dropping DN/name or serializing the password would break safe replay."""
        from net.domain.tasks import enqueue_domain_operation

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.RESET_PASSWORD,
            target_ids=[self.second.pk, self.first.pk],
            parameters={},
            password='Fresh-Password-Only-In-Memory!',
        )

        task = operation.task
        snapshots = list(task.target_runs.order_by('target_id').values_list(
            'target_snapshot', flat=True,
        ))
        serialized = json.dumps({
            'profile': task.profile_snapshot,
            'parameters': task.parameters_snapshot,
            'scope': task.target_scope_snapshot,
            'targets': snapshots,
            'audit': operation.parameter_summary,
        }, ensure_ascii=False)

        self.assertEqual(task.task_type, TaskRun.TaskType.DOMAIN_OPERATION)
        self.assertEqual(task.total_targets, 2)
        self.assertEqual(operation.target_count, 2)
        self.assertTrue(all(snapshot['distinguished_name'] for snapshot in snapshots))
        self.assertTrue(all(snapshot['name'] for snapshot in snapshots))
        self.assertNotIn('Fresh-Password-Only-In-Memory!', serialized)
        self.assertNotIn('encrypted_payload', serialized)
        self.assertNotIn('"password":', serialized.casefold())

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_create_user_queues_one_unsynced_target_with_deterministic_identity(self):
        """Looking up Domain_Account would prevent creation and misidentify the audit target."""
        from net.domain.tasks import enqueue_domain_operation

        Domain_Account.objects.all().delete()
        parameters = {
            'user_dn': 'CN=New User,OU=Users,DC=example,DC=test',
            'login_name': 'new.user',
            'display_name': 'New User',
        }

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.CREATE_USER,
            target_ids=[],
            parameters=parameters,
            password='Initial-Password-Only-In-Secret!',
        )

        task = operation.task
        target = task.target_runs.get()
        serialized = json.dumps({
            'profile': task.profile_snapshot,
            'parameters': task.parameters_snapshot,
            'scope': task.target_scope_snapshot,
            'target': target.target_snapshot,
            'result': target.result_snapshot,
            'audit': operation.parameter_summary,
            'message': target.error_message,
        }, ensure_ascii=False)
        self.assertEqual(task.total_targets, 1)
        self.assertEqual(operation.target_count, 1)
        self.assertEqual(UUID(target.target_id).version, 5)
        self.assertEqual(target.target_snapshot, {
            'id': target.target_id,
            'distinguished_name': parameters['user_dn'],
            'name': parameters['display_name'],
            'login_name': parameters['login_name'],
        })
        self.assertEqual(task.parameters_snapshot, parameters)
        self.assertEqual(operation.parameter_summary, parameters)
        self.assertNotIn('Initial-Password-Only-In-Secret!', serialized)
        self.assertNotIn('encrypted_payload', serialized)
        self.assertNotIn('password', serialized.casefold())

    def test_duplicate_active_action_and_target_scope_is_rejected(self):
        """Allowing a second active same-target write would race AD changes."""
        from net.domain.tasks import enqueue_domain_operation

        kwargs = {
            'requested_by': self.user,
            'object_type': DomainOperation.ObjectType.ACCOUNT,
            'action': DomainOperation.Action.DISABLE,
            'target_ids': [self.first.pk, self.second.pk],
            'parameters': {},
        }
        enqueue_domain_operation(**kwargs)

        with self.assertRaises(ValidationError):
            enqueue_domain_operation(**kwargs)

    def test_different_actions_on_the_same_target_are_serialized_across_tasks(self):
        """Another active action on one DN must not race the first LDAP write."""
        from net.domain.tasks import enqueue_domain_operation

        common = {
            'requested_by': self.user,
            'object_type': DomainOperation.ObjectType.ACCOUNT,
            'target_ids': [self.first.pk, self.second.pk],
            'parameters': {},
        }
        disable = enqueue_domain_operation(
            **common, action=DomainOperation.Action.DISABLE,
        )

        with self.assertRaises(ValidationError):
            enqueue_domain_operation(
                **common, action=DomainOperation.Action.ENABLE,
            )
        self.assertEqual(disable.task.status, TaskRun.Status.QUEUED)

    def test_partially_overlapping_active_target_scope_is_rejected(self):
        """Allowing [A, B] with [A, C] would race writes to A across Workers."""
        from net.domain.tasks import enqueue_domain_operation

        enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE,
            target_ids=[self.first.pk, self.second.pk], parameters={},
        )

        with self.assertRaises(ValidationError):
            enqueue_domain_operation(
                requested_by=self.user,
                object_type=DomainOperation.ObjectType.ACCOUNT,
                action=DomainOperation.Action.DISABLE,
                target_ids=[self.first.pk, self.third.pk], parameters={},
            )

    def test_enqueue_refuses_to_run_without_the_global_domain_lock_row(self):
        """Silently proceeding without the singleton lock would permit races."""
        from net.domain.tasks import enqueue_domain_operation

        Domain_Controller_Config.objects.all().delete()

        with self.assertRaises(ValidationError):
            enqueue_domain_operation(
                requested_by=self.user,
                object_type=DomainOperation.ObjectType.ACCOUNT,
                action=DomainOperation.Action.DISABLE,
                target_ids=[self.first.pk], parameters={},
            )

    def test_normalized_dn_overlap_is_rejected_even_for_different_object_ids(self):
        """Comparing raw DN text would miss case-only aliases for the same AD object."""
        from net.domain.tasks import enqueue_domain_operation

        alias = Domain_Account.objects.create(
            account_name='First Account Alias',
            login_name='first.account.alias',
            distinguished_name='cn=first account,ou=users,dc=example,dc=test',
        )
        enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE,
            target_ids=[self.first.pk], parameters={},
        )

        with self.assertRaises(ValidationError):
            enqueue_domain_operation(
                requested_by=self.user,
                object_type=DomainOperation.ObjectType.ACCOUNT,
                action=DomainOperation.Action.DISABLE,
                target_ids=[alias.pk], parameters={},
            )

    def test_different_domain_dns_can_queue_with_distinct_execution_scopes(self):
        """A per-DN constraint must not serialize independent directory objects."""
        from net.domain.tasks import enqueue_domain_operation

        first = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE,
            target_ids=[self.first.pk], parameters={},
        )
        second = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.ENABLE,
            target_ids=[self.second.pk], parameters={},
        )

        first_scope = first.task.target_runs.get().execution_scope_key
        second_scope = second.task.target_runs.get().execution_scope_key
        self.assertRegex(first_scope, r'^[0-9a-f]{64}$')
        self.assertRegex(second_scope, r'^[0-9a-f]{64}$')
        self.assertNotEqual(first_scope, second_scope)

    def test_database_scope_conflict_becomes_a_redacted_validation_error(self):
        """An IntegrityError after an independent preflight must not leak database details."""
        from net.domain.tasks import enqueue_domain_operation

        alias = Domain_Account.objects.create(
            account_name='First Account Alias',
            login_name='first.account.alias',
            distinguished_name='cn=first account,ou=users,dc=example,dc=test',
        )
        enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE,
            target_ids=[self.first.pk], parameters={},
        )

        with patch('net.domain.tasks._reject_active_domain_target_overlap'):
            with self.assertRaises(ValidationError) as caught:
                enqueue_domain_operation(
                    requested_by=self.user,
                    object_type=DomainOperation.ObjectType.ACCOUNT,
                    action=DomainOperation.Action.ENABLE,
                    target_ids=[alias.pk], parameters={},
                )

        message = str(caught.exception).casefold()
        self.assertIn('活动域控操作', message)
        self.assertNotIn('unique', message)
        self.assertNotIn('sqlite', message)


class DomainOperationWorkerTests(TransactionTestCase):
    """One Worker aggregates mixed target outcomes without a live LDAP server."""

    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='domain-worker', password='local-test-password',
        )
        self.accounts = [
            Domain_Account.objects.create(
                account_name=name.title(),
                login_name=name,
                distinguished_name=f'CN={name},OU=Users,DC=example,DC=test',
            )
            for name in ('alpha.account', 'beta.account', 'gamma.account')
        ]
        Domain_Controller_Config.objects.create(
            host='dc.example.test', base_dn='DC=example,DC=test',
            bind_username='worker', bind_password='not-a-real-directory-secret',
        )

    def test_worker_marks_mixed_domain_results_partial_and_retry_uses_only_failed_target(self):
        """Aggregating all-success or retrying successes would repeat directory writes."""
        from net.domain.tasks import enqueue_domain_operation, retry_failed_domain_operation
        from net.inspections.worker import TaskWorker
        from net.domain.client import DomainActionResult

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE,
            target_ids=[account.pk for account in self.accounts],
            parameters={},
        )

        outcomes = iter((
            DomainActionResult(True),
            DomainActionResult(True),
            DomainActionResult(False, 'bind_password=should-not-appear'),
        ))
        with patch('net.domain.executor.DomainClient.execute', side_effect=lambda *args, **kwargs: next(outcomes)):
            self.assertTrue(TaskWorker(worker_id='domain-worker', threads=2, lease_seconds=30).run_once())

        operation.refresh_from_db()
        task = operation.task
        task.refresh_from_db()
        statuses = list(task.target_runs.order_by('target_id').values_list('status', flat=True))

        self.assertEqual(task.status, TaskRun.Status.PARTIAL)
        self.assertEqual(operation.status, DomainOperation.Status.PARTIAL)
        self.assertEqual(statuses.count(TaskRun.Status.SUCCESS), 2)
        self.assertEqual(statuses.count(TaskRun.Status.FAILED), 1)
        self.assertNotIn('bind_password', task.error_summary)

        retry = retry_failed_domain_operation(operation.pk, requested_by=self.user)
        self.assertEqual(retry.task.total_targets, 1)
        self.assertEqual(retry.task.target_runs.get().target_id, next(
            target.target_id for target in task.target_runs.all()
            if target.status == TaskRun.Status.FAILED
        ))

    def test_successful_move_and_disable_update_local_account_mirror(self):
        """Leaving the old DN or active flag would make the next queued action target stale data."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker

        account = self.accounts[0]
        destination = 'OU=Managed,DC=example,DC=test'
        moved_dn = f'CN=alpha.account,{destination}'
        move = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.MOVE_OU, target_ids=[account.pk],
            parameters={'destination_dn': destination},
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(True, details={
                'distinguished_name': moved_dn, 'ou': destination,
            }),
        ):
            TaskWorker(worker_id='mirror-move-worker', threads=1, lease_seconds=30).run_once()
        account.refresh_from_db()
        self.assertEqual(account.distinguished_name, moved_dn)
        self.assertEqual(account.ou, destination)

        disable = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE, target_ids=[account.pk], parameters={},
        )
        self.assertEqual(
            disable.task.target_runs.get().target_snapshot['distinguished_name'],
            moved_dn,
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(True),
        ) as execute:
            TaskWorker(worker_id='mirror-disable-worker', threads=1, lease_seconds=30).run_once()
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        self.assertEqual(execute.call_args.args[2], moved_dn)
        self.assertEqual(move.task.target_runs.get().status, TaskRun.Status.SUCCESS)
        self.assertEqual(disable.task.target_runs.get().status, TaskRun.Status.SUCCESS)

    def test_successful_computer_move_updates_dn_and_ou(self):
        """Updating moved accounts but not computers would leave later computer actions on the old DN."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker

        computer = Domain_Computer.objects.create(
            computer_name='PC-MOVE',
            distinguished_name='CN=PC-MOVE,OU=Computers,DC=example,DC=test',
        )
        destination = 'OU=Managed,DC=example,DC=test'
        moved_dn = f'CN=PC-MOVE,{destination}'
        operation = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.COMPUTER,
            action=DomainOperation.Action.MOVE_OU, target_ids=[computer.pk],
            parameters={'destination_dn': destination},
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(True, details={
                'distinguished_name': moved_dn, 'ou': destination,
            }),
        ):
            TaskWorker(worker_id='computer-move-worker', threads=1, lease_seconds=30).run_once()

        computer.refresh_from_db()
        self.assertEqual(computer.distinguished_name, moved_dn)
        self.assertEqual(computer.ou, destination)
        self.assertEqual(
            operation.task.target_runs.get().result_snapshot['mirror_status'],
            'updated',
        )

    def test_successful_computer_enable_updates_local_mirror(self):
        """Updating only accounts would leave domain computers stale after enable or disable."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker

        computer = Domain_Computer.objects.create(
            computer_name='PC-MIRROR', is_active=False,
            distinguished_name='CN=PC-MIRROR,OU=Computers,DC=example,DC=test',
        )
        operation = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.COMPUTER,
            action=DomainOperation.Action.ENABLE, target_ids=[computer.pk], parameters={},
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(True),
        ):
            TaskWorker(worker_id='computer-mirror-worker', threads=1, lease_seconds=30).run_once()

        computer.refresh_from_db()
        self.assertTrue(computer.is_active)
        self.assertEqual(operation.task.target_runs.get().status, TaskRun.Status.SUCCESS)

    def test_stale_local_mirror_does_not_turn_ldap_success_into_failure(self):
        """A concurrent sync changing the local DN must not rewrite LDAP success as task failure."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker

        account = self.accounts[0]
        operation = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE, target_ids=[account.pk], parameters={},
        )
        account.distinguished_name = 'CN=alpha.account,OU=Synced,DC=example,DC=test'
        account.save(update_fields=['distinguished_name'])
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(True),
        ):
            TaskWorker(worker_id='stale-mirror-worker', threads=1, lease_seconds=30).run_once()

        target = operation.task.target_runs.get()
        account.refresh_from_db()
        self.assertEqual(target.status, TaskRun.Status.SUCCESS)
        self.assertTrue(account.is_active)
        self.assertEqual(target.result_snapshot['mirror_status'], 'stale_waiting_for_sync')

    def test_missing_local_mirror_does_not_turn_ldap_success_into_failure(self):
        """A deleted local row must leave a successful LDAP write available for later synchronization."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker

        account = self.accounts[0]
        operation = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE, target_ids=[account.pk], parameters={},
        )
        account.delete()
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(True),
        ):
            TaskWorker(worker_id='missing-mirror-worker', threads=1, lease_seconds=30).run_once()

        target = operation.task.target_runs.get()
        self.assertEqual(target.status, TaskRun.Status.SUCCESS)
        self.assertEqual(target.result_snapshot['mirror_status'], 'missing_waiting_for_sync')

    def test_local_mirror_save_error_does_not_turn_ldap_success_into_failure(self):
        """A local persistence conflict must preserve the LDAP outcome and request a later sync."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker

        account = self.accounts[0]
        operation = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE, target_ids=[account.pk], parameters={},
        )
        with (
            patch(
                'net.domain.executor.DomainClient.execute',
                return_value=DomainActionResult(True),
            ),
            patch.object(
                Domain_Account, 'save',
                side_effect=ValidationError('simulated local mirror conflict'),
            ),
        ):
            TaskWorker(worker_id='mirror-save-error-worker', threads=1, lease_seconds=30).run_once()

        target = operation.task.target_runs.get()
        self.assertEqual(target.status, TaskRun.Status.SUCCESS)
        self.assertEqual(target.result_snapshot['mirror_status'], 'update_failed_waiting_for_sync')

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_create_user_virtual_target_never_queries_local_account_mirror(self):
        """A deterministic virtual target UUID is not a Domain_Account primary key to mirror."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker

        operation = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.CREATE_USER, target_ids=[],
            parameters={
                'user_dn': 'CN=Virtual User,OU=Users,DC=example,DC=test',
                'login_name': 'virtual.user', 'display_name': 'Virtual User',
            }, password='Virtual-User-Password!',
        )
        with (
            patch(
                'net.domain.executor.DomainClient.execute',
                return_value=DomainActionResult(True),
            ),
            patch.object(
                Domain_Account.objects, 'select_for_update',
                side_effect=AssertionError('create_user must not query Domain_Account'),
            ),
        ):
            TaskWorker(worker_id='virtual-user-worker', threads=1, lease_seconds=30).run_once()

        self.assertEqual(operation.task.target_runs.get().status, TaskRun.Status.SUCCESS)

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_create_user_retry_carries_only_trusted_pending_stage_and_requires_new_password(self):
        """A retry must distinguish verified password completion from an uncertain LDAP add."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation, retry_failed_domain_operation
        from net.inspections.worker import TaskWorker

        user_dn = 'CN=Pending Retry,OU=Users,DC=example,DC=test'
        operation = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.CREATE_USER, target_ids=[],
            parameters={
                'user_dn': user_dn,
                'login_name': 'pending.retry', 'display_name': 'Pending Retry',
            }, password='First-Consumed-Password!',
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(
                False,
                '用户已创建，但密码初始化失败；请提交新密码后重试。',
                stage='user_created_password_pending',
                details={'distinguished_name': user_dn},
            ),
        ):
            TaskWorker(worker_id='pending-create-worker', threads=1, lease_seconds=30).run_once()

        operation.refresh_from_db()
        operation.task.refresh_from_db()
        target = operation.task.target_runs.get()
        self.assertEqual(operation.status, DomainOperation.Status.FAILED)
        self.assertEqual(operation.task.status, TaskRun.Status.FAILED)
        self.assertEqual(target.status, TaskRun.Status.FAILED)
        self.assertEqual(target.result_snapshot['stage'], 'user_created_password_pending')
        self.assertEqual(target.result_snapshot['distinguished_name'], user_dn)
        self.assertNotIn('First-Consumed-Password!', json.dumps(target.result_snapshot))

        with self.assertRaises(ValidationError):
            retry_failed_domain_operation(operation.pk, requested_by=self.user)
        original_result = dict(target.result_snapshot)
        target.result_snapshot = {
            **original_result,
            'distinguished_name': 'CN=Different User,OU=Users,DC=example,DC=test',
        }
        TaskTargetRun.objects.filter(pk=target.pk).update(result_snapshot=target.result_snapshot)
        with self.assertRaisesMessage(ValidationError, '恢复阶段无法验证'):
            retry_failed_domain_operation(
                operation.pk, requested_by=self.user, password='Must-Not-Resume-Wrong-DN!',
            )
        target.result_snapshot = original_result
        TaskTargetRun.objects.filter(pk=target.pk).update(result_snapshot=target.result_snapshot)
        retry = retry_failed_domain_operation(
            operation.pk, requested_by=self.user, password='Fresh-Retry-Password!',
        )
        self.assertEqual(
            retry.task.target_runs.get().target_snapshot['recovery_stage'],
            'user_created_password_pending',
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(True),
        ) as execute:
            TaskWorker(worker_id='pending-retry-worker', threads=1, lease_seconds=30).run_once()
        retry.refresh_from_db()
        retry.task.refresh_from_db()
        self.assertEqual(
            execute.call_args.kwargs['recovery_stage'],
            'user_created_password_pending',
        )
        self.assertEqual(retry.status, DomainOperation.Status.SUCCESS)
        self.assertEqual(retry.task.status, TaskRun.Status.SUCCESS)

        uncertain_dn = 'CN=Uncertain Retry,OU=Users,DC=example,DC=test'
        uncertain = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.CREATE_USER, target_ids=[],
            parameters={
                'user_dn': uncertain_dn,
                'login_name': 'uncertain.retry', 'display_name': 'Uncertain Retry',
            }, password='Uncertain-First-Password!',
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(
                False, '新增用户结果不确定，需要人工核查。',
                stage='manual_intervention_required',
                details={'distinguished_name': uncertain_dn},
            ),
        ):
            TaskWorker(worker_id='uncertain-create-worker', threads=1, lease_seconds=30).run_once()

        uncertain.refresh_from_db()
        uncertain.task.refresh_from_db()
        uncertain_target = uncertain.task.target_runs.get()
        self.assertEqual(uncertain.status, DomainOperation.Status.FAILED)
        self.assertEqual(uncertain.task.status, TaskRun.Status.FAILED)
        self.assertEqual(uncertain_target.result_snapshot['stage'], 'manual_intervention_required')
        self.assertNotIn('Uncertain-First-Password!', json.dumps(uncertain_target.result_snapshot))
        with self.assertRaisesMessage(ValidationError, '需要人工核查'):
            retry_failed_domain_operation(
                uncertain.pk, requested_by=self.user, password='Never-Use-This!',
            )

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_create_user_retry_rejects_pending_stage_for_a_different_dn(self):
        """A stage without the same audited DN is not trustworthy enough to skip add."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation, retry_failed_domain_operation
        from net.inspections.worker import TaskWorker

        operation = enqueue_domain_operation(
            requested_by=self.user, object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.CREATE_USER, target_ids=[],
            parameters={
                'user_dn': 'CN=Expected,OU=Users,DC=example,DC=test',
                'login_name': 'expected.user', 'display_name': 'Expected User',
            }, password='Expected-First-Password!',
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(
                False,
                '用户已创建，但密码初始化失败；请提交新密码后重试。',
                stage='user_created_password_pending',
                details={
                    'distinguished_name': 'CN=Different,OU=Users,DC=example,DC=test',
                },
            ),
        ):
            TaskWorker(worker_id='mismatched-stage-worker', threads=1, lease_seconds=30).run_once()

        with self.assertRaisesMessage(ValidationError, '需要人工核查'):
            retry_failed_domain_operation(
                operation.pk,
                requested_by=self.user,
                password='Must-Not-Resume-Different-Dn!',
            )

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_password_context_is_consumed_once_before_worker_threads(self):
        """Re-reading the secret in target threads could reuse a password after a crash."""
        from net.domain.tasks import enqueue_domain_operation
        from net.domain.executor import prepare_domain_task_context
        from net.inspections.queue import claim_next_task

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.RESET_PASSWORD,
            target_ids=[self.accounts[0].pk], parameters={},
            password='One-Time-Password-For-Worker-Only!',
        )
        task = claim_next_task('password-context-worker', lease_seconds=30)

        context = prepare_domain_task_context(
            task,
            worker_id='password-context-worker',
            claim_generation=task.attempt_count,
        )

        self.assertEqual(context.password, 'One-Time-Password-For-Worker-Only!')
        self.assertFalse(DomainOperation.objects.filter(pk=operation.pk, secret__isnull=False).exists())
        with self.assertRaises(ValidationError):
            prepare_domain_task_context(
                task,
                worker_id='password-context-worker',
                claim_generation=task.attempt_count,
            )

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_create_user_worker_uses_the_same_unsynced_dn_and_consumes_secret_once(self):
        """Using a placeholder account DN would create one object and audit another."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker

        user_dn = 'CN=Worker New User,OU=Users,DC=example,DC=test'
        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.CREATE_USER,
            target_ids=[],
            parameters={
                'user_dn': user_dn,
                'login_name': 'worker.new.user',
                'display_name': 'Worker New User',
            },
            password='Worker-Initial-Password-Consumed-Once!',
        )

        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(True),
        ) as execute:
            self.assertTrue(
                TaskWorker(
                    worker_id='create-user-worker', threads=1, lease_seconds=30,
                ).run_once(),
            )

        operation.refresh_from_db()
        task = operation.task
        task.refresh_from_db()
        target = task.target_runs.get()
        args = execute.call_args.args
        self.assertEqual(args[2], user_dn)
        self.assertEqual(args[3]['user_dn'], user_dn)
        self.assertEqual(execute.call_args.kwargs['password'], 'Worker-Initial-Password-Consumed-Once!')
        self.assertEqual(target.target_snapshot['distinguished_name'], user_dn)
        self.assertEqual(target.status, TaskRun.Status.SUCCESS)
        self.assertEqual(task.status, TaskRun.Status.SUCCESS)
        self.assertEqual(operation.status, DomainOperation.Status.SUCCESS)
        self.assertFalse(DomainOperation.objects.filter(pk=operation.pk, secret__isnull=False).exists())

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_unclaimed_task_cannot_consume_its_password_context(self):
        """A caller bypassing the Worker lease must not burn a queued secret."""
        from net.domain.tasks import enqueue_domain_operation
        from net.domain.executor import prepare_domain_task_context

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.RESET_PASSWORD,
            target_ids=[self.accounts[0].pk], parameters={},
            password='Queued-Password-Must-Not-Be-Consumed!',
        )

        with self.assertRaises(ValidationError):
            prepare_domain_task_context(
                operation.task, worker_id='unclaimed-worker', claim_generation=0,
            )

        self.assertTrue(DomainOperation.objects.filter(pk=operation.pk, secret__isnull=False).exists())

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_wrong_worker_cannot_consume_a_claimed_password_context(self):
        """Checking only lease freshness would let another Worker burn the secret."""
        from net.domain.tasks import enqueue_domain_operation
        from net.domain.executor import prepare_domain_task_context
        from net.inspections.queue import claim_next_task

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.RESET_PASSWORD,
            target_ids=[self.accounts[0].pk], parameters={},
            password='Claimed-Password-Must-Stay-Private!',
        )
        task = claim_next_task('secret-owner', lease_seconds=30)

        with self.assertRaises(ValidationError):
            prepare_domain_task_context(
                task, worker_id='other-worker', claim_generation=task.attempt_count,
            )

        self.assertTrue(DomainOperation.objects.filter(pk=operation.pk, secret__isnull=False).exists())

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_old_claim_generation_cannot_consume_a_password_context(self):
        """A stale generation must not consume the current claim's one-time secret."""
        from net.domain.tasks import enqueue_domain_operation
        from net.domain.executor import prepare_domain_task_context
        from net.inspections.queue import claim_next_task

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.RESET_PASSWORD,
            target_ids=[self.accounts[0].pk], parameters={},
            password='Stale-Generation-Must-Stay-Private!',
        )
        task = claim_next_task('generation-owner', lease_seconds=30)

        with self.assertRaises(ValidationError):
            prepare_domain_task_context(
                task, worker_id='generation-owner', claim_generation=task.attempt_count - 1,
            )

        self.assertTrue(DomainOperation.objects.filter(pk=operation.pk, secret__isnull=False).exists())

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_stop_after_password_consumption_fails_unsubmitted_targets(self):
        """Leaving queued targets recoverable after deletion would require an unavailable password."""
        from net.domain.tasks import enqueue_domain_operation
        from net.domain.executor import prepare_domain_task_context
        from net.inspections.worker import TaskWorker

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.RESET_PASSWORD,
            target_ids=[self.accounts[0].pk, self.accounts[1].pk], parameters={},
            password='Stop-Window-Password-Must-Not-Persist!',
        )
        stop_event = threading.Event()

        def consume_then_stop(*args, **kwargs):
            context = prepare_domain_task_context(*args, **kwargs)
            stop_event.set()
            return context

        with patch('net.inspections.worker.prepare_domain_task_context', side_effect=consume_then_stop):
            TaskWorker(worker_id='stop-window-worker', threads=2, lease_seconds=30).run_once(stop_event)

        operation.refresh_from_db()
        task = operation.task
        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.FAILED)
        self.assertEqual(operation.status, DomainOperation.Status.FAILED)
        self.assertEqual(
            list(task.target_runs.values_list('status', flat=True)),
            [TaskRun.Status.FAILED, TaskRun.Status.FAILED],
        )
        self.assertTrue(all(
            target.error_message == '密码操作已中断，请重新提交'
            for target in task.target_runs.all()
        ))

    def test_worker_serializes_targets_with_the_same_distinguished_name(self):
        """Submitting equal DNs together would cause conflicting LDAP writes."""
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.worker import TaskWorker
        from net.domain.client import DomainActionResult

        self.accounts[1].distinguished_name = self.accounts[0].distinguished_name
        self.accounts[1].save(update_fields=['distinguished_name'])
        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE,
            target_ids=[account.pk for account in self.accounts[:2]], parameters={},
        )
        active = 0
        peak = 0
        mutex = threading.Lock()

        def execute(*args, **kwargs):
            nonlocal active, peak
            with mutex:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with mutex:
                active -= 1
            return DomainActionResult(True)

        with patch('net.domain.executor.DomainClient.execute', side_effect=execute):
            TaskWorker(worker_id='same-dn-worker', threads=2, lease_seconds=30).run_once()

        operation.refresh_from_db()
        self.assertEqual(operation.status, DomainOperation.Status.SUCCESS)
        self.assertEqual(peak, 1)

    def test_lease_recovery_aggregates_a_terminal_domain_operation(self):
        """A recovered terminal task must not leave its audit record running."""
        from net.domain.tasks import enqueue_domain_operation
        from net.inspections.queue import claim_next_task, recover_expired_tasks

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE,
            target_ids=[self.accounts[0].pk], parameters={},
        )
        claimed = claim_next_task('lost-domain-worker', lease_seconds=30)
        target = claimed.target_runs.get()
        target.status = TaskRun.Status.SUCCESS
        target.started_at = timezone.now()
        target.finished_at = timezone.now()
        target.save()

        recover_expired_tasks(now=claimed.lease_expires_at + timedelta(seconds=1))

        operation.refresh_from_db()
        self.assertEqual(operation.status, DomainOperation.Status.SUCCESS)
        self.assertIsNotNone(operation.finished_at)

    def test_concurrent_overlapping_enqueues_admit_only_one_operation(self):
        """A global queue lock must serialize racing [A, B] and [A, C] submissions."""
        from net.domain.tasks import enqueue_domain_operation

        gate = threading.Barrier(3)
        outcomes = []
        result_lock = threading.Lock()

        def enqueue(target_ids):
            close_old_connections()
            try:
                gate.wait()
                enqueue_domain_operation(
                    requested_by=self.user,
                    object_type=DomainOperation.ObjectType.ACCOUNT,
                    action=DomainOperation.Action.DISABLE,
                    target_ids=target_ids, parameters={},
                )
                outcome = 'accepted'
            except ValidationError:
                outcome = 'rejected'
            finally:
                with result_lock:
                    outcomes.append(outcome)
                connections.close_all()

        first = threading.Thread(target=enqueue, args=([self.accounts[0].pk, self.accounts[1].pk],))
        second = threading.Thread(target=enqueue, args=([self.accounts[0].pk, self.accounts[2].pk],))
        first.start()
        second.start()
        gate.wait()
        first.join()
        second.join()

        self.assertEqual(sorted(outcomes), ['accepted', 'rejected'])
        self.assertEqual(TaskRun.objects.filter(
            task_type=TaskRun.TaskType.DOMAIN_OPERATION,
            status__in=TaskRun.ACTIVE_STATUSES,
        ).count(), 1)

    def test_stale_worker_cannot_mark_a_reclaimed_operation_running(self):
        """A stale owner must not overwrite the audit mirror after lease recovery."""
        from net.domain.tasks import enqueue_domain_operation
        from net.domain.executor import mark_domain_operation_running
        from net.inspections.queue import claim_next_task, recover_expired_tasks

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.DISABLE,
            target_ids=[self.accounts[0].pk], parameters={},
        )
        first = claim_next_task('old-worker', lease_seconds=30)
        recover_expired_tasks(now=first.lease_expires_at + timedelta(seconds=1))
        claim_next_task('new-worker', lease_seconds=30)

        self.assertFalse(mark_domain_operation_running(
            first, worker_id='old-worker', claim_generation=first.attempt_count,
        ))
        operation.refresh_from_db()
        self.assertEqual(operation.status, DomainOperation.Status.QUEUED)

    @override_settings(DOMAIN_OPERATION_ENCRYPTION_KEY='V6NyMLdzoMKswUV33psN2GpZlkAhw4y8ao_0J2lCp7E=')
    def test_password_failure_retry_requires_a_new_password(self):
        """Retrying with consumed material would silently replay a deleted credential."""
        from net.domain.client import DomainActionResult
        from net.domain.tasks import enqueue_domain_operation, retry_failed_domain_operation
        from net.inspections.worker import TaskWorker

        operation = enqueue_domain_operation(
            requested_by=self.user,
            object_type=DomainOperation.ObjectType.ACCOUNT,
            action=DomainOperation.Action.RESET_PASSWORD,
            target_ids=[self.accounts[0].pk], parameters={},
            password='First-Password-Is-Consumed-Once!',
        )
        with patch(
            'net.domain.executor.DomainClient.execute',
            return_value=DomainActionResult(False, 'LDAP 操作失败。'),
        ):
            TaskWorker(worker_id='password-failure-worker', threads=1, lease_seconds=30).run_once()

        with self.assertRaises(ValidationError):
            retry_failed_domain_operation(operation.pk, requested_by=self.user)
        retry = retry_failed_domain_operation(
            operation.pk,
            requested_by=self.user,
            password='Second-Password-Is-A-New-Submission!',
        )
        self.assertTrue(DomainOperation.objects.filter(pk=retry.pk, secret__isnull=False).exists())


class DomainExecutionScopeConstraintTests(TransactionTestCase):
    """The database, rather than a process-local lock, fences a canonical DN."""

    def _domain_task(self, target_id):
        return TaskRun.objects.create(
            task_type=TaskRun.TaskType.DOMAIN_OPERATION,
            source=TaskRun.Source.MANUAL,
            profile_snapshot={
                'object_type': DomainOperation.ObjectType.ACCOUNT,
                'action': DomainOperation.Action.DISABLE,
            },
            parameters_snapshot={},
            selected_items_snapshot=[],
            target_scope_snapshot={'targets': [{
                'target_type': TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
                'target_id': target_id,
            }]},
            total_targets=1,
        )

    def test_second_database_connection_cannot_reserve_an_active_dn_scope(self):
        """Removing the DB constraint would let separate Worker processes both proceed."""
        first_task = self._domain_task('first-target')
        second_task = self._domain_task('second-target')
        scope = 'b' * 64
        TaskTargetRun.objects.create(
            task=first_task,
            target_type=TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
            target_id='first-target',
            target_snapshot={'distinguished_name': 'CN=First,OU=Users,DC=example,DC=test'},
            execution_scope_key=scope,
        )
        outcomes = []

        def reserve_from_another_connection():
            close_old_connections()
            try:
                with transaction.atomic():
                    TaskTargetRun.objects.create(
                        task=second_task,
                        target_type=TaskTargetRun.TargetType.DOMAIN_ACCOUNT,
                        target_id='second-target',
                        target_snapshot={
                            'distinguished_name': 'CN=First,OU=Users,DC=example,DC=test',
                        },
                        execution_scope_key=scope,
                    )
            except IntegrityError:
                outcomes.append('conflict')
            finally:
                connections.close_all()

        contender = threading.Thread(target=reserve_from_another_connection)
        contender.start()
        contender.join()

        self.assertEqual(outcomes, ['conflict'])
