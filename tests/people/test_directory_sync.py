from importlib import import_module
from dataclasses import replace
import json
from unittest.mock import patch

from django.test import TestCase
from django.utils.crypto import salted_hmac
from django.db import IntegrityError

from net.people.directory import DirectoryPerson
from net.models import People, PeopleSyncSource


def _sync_module():
    try:
        return import_module('net.people.directory.sync')
    except ModuleNotFoundError:
        return None


def _adapter_configuration_identity(source):
    """Independent fixture implementation of the explicit adapter contract."""
    encoded = json.dumps({
        'source_key': source.source_key,
        'source_type': source.source_type,
        'root_department_ids': list(source.root_department_ids),
        'is_enabled': source.is_enabled,
        'credentials': source.credentials,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {
        'source_key': source.source_key,
        'digest': salted_hmac('net.people.directory.fetch-configuration', encoded).hexdigest(),
    }


class SnapshotAdapter:
    """A complete, in-memory directory snapshot with no external I/O."""

    def __init__(self, source, people, *, complete=True, skipped_records=(), source_key=None,
                 before_iter=None):
        self.source_key = source.source_key if source_key is None else source_key
        self.fetch_configuration_identity = _adapter_configuration_identity(source)
        self._people = tuple(people)
        self._complete = complete
        self.skipped_records = tuple(skipped_records)
        self._before_iter = before_iter
        self.iter_calls = 0
        self.last_snapshot_complete = False

    def iter_people(self):
        self.iter_calls += 1
        if self._before_iter is not None:
            self._before_iter()
        for person in self._people:
            yield person
        self.last_snapshot_complete = self._complete


class ProviderResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def json(self):
        return self.payload


class ProviderSession:
    def __init__(self, handler):
        self.handler = handler

    def get(self, url, **kwargs):
        return self.handler('GET', url, kwargs)

    def post(self, url, **kwargs):
        return self.handler('POST', url, kwargs)


class PeopleSyncPreviewTests(TestCase):
    def source(self, **overrides):
        values = {
            'source_type': 'feishu',
            'name': '总部目录',
            'source_key': 'feishu-hq',
            'credentials': {'app_id': 'fixture-app', 'app_secret': 'fixture-secret'},
            'root_department_ids': ['root'],
        }
        values.update(overrides)
        source = PeopleSyncSource(**values)
        source.full_clean()
        source.save()
        return source

    def test_preview_lists_a_new_employee_without_writing_personnel_or_source_state(self):
        """Would fail if preview persisted a create or changed sync metadata."""
        sync = _sync_module()
        self.assertIsNotNone(sync, '人员同步服务必须提供 sync 模块。')
        source = self.source()
        before_source_sync = source.last_synced_at
        adapter = SnapshotAdapter(source, [
            DirectoryPerson(
                employee_id='EMP-100', name='王工', email='wang@example.test',
                department='运维', leader='李主管', external_user_id='ou-100',
            ),
        ])

        preview = sync.preview_people_sync(source, adapter)

        self.assertTrue(preview.is_valid)
        self.assertEqual(preview.creates, ({
            'employee_id': 'EMP-100', 'name': '王工', 'email': 'wang@example.test',
            'phone': '',
            'department': '运维', 'leader': '李主管', 'external_user_id': 'ou-100',
        },))
        self.assertEqual(preview.updates, ())
        self.assertEqual(preview.deactivations, ())
        self.assertTrue(preview.token)
        self.assertEqual(People.objects.count(), 0)
        source.refresh_from_db()
        self.assertEqual(source.last_synced_at, before_source_sync)

    def test_apply_creates_the_previewed_employee_and_records_sync_metadata(self):
        """Would fail if apply did not materialize the signed create exactly once."""
        sync = _sync_module()
        self.assertIsNotNone(sync)
        self.assertTrue(hasattr(sync, 'apply_people_sync'),
                        '人员同步服务必须提供 apply_people_sync。')
        source = self.source()
        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-101', name='赵工', external_user_id='ou-101'),
        ]))

        result = sync.apply_people_sync(source, preview)

        person = People.objects.get(employee_id='EMP-101')
        self.assertEqual(person.name, '赵工')
        self.assertEqual(person.source, 'feishu')
        self.assertEqual(person.sync_source_id, source.pk)
        self.assertEqual(person.platform_user_id, 'ou-101')
        self.assertTrue(person.is_active)
        self.assertIsNotNone(person.last_synced_at)
        source.refresh_from_db()
        self.assertIsNotNone(source.last_synced_at)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.deactivated, 0)

    def test_updates_employee_owned_by_the_exact_same_source_then_a_repeat_is_noop(self):
        """Would fail if employee matching transferred ownership or repeated a change."""
        sync = _sync_module()
        source = self.source()
        person = People.objects.create(
            employee_id='EMP-200', name='旧姓名', department='旧部门', source='feishu',
            sync_source=source, platform_user_id='ou-200', is_active=False,
        )

        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-200', name='新姓名', department='新部门',
                            external_user_id='ou-200'),
        ]))

        self.assertTrue(preview.is_valid)
        self.assertEqual(len(preview.updates), 1)
        self.assertEqual(preview.updates[0]['employee_id'], 'EMP-200')
        result = sync.apply_people_sync(source, preview)
        person.refresh_from_db()
        self.assertEqual((result.created, result.updated, result.deactivated), (0, 1, 0))
        self.assertEqual((person.name, person.department, person.is_active), ('新姓名', '新部门', True))

        repeat = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-200', name='新姓名', department='新部门',
                            external_user_id='ou-200'),
        ]))
        repeat_result = sync.apply_people_sync(source, repeat)
        self.assertEqual((repeat_result.created, repeat_result.updated, repeat_result.deactivated), (0, 0, 0))
        self.assertEqual(People.objects.filter(employee_id='EMP-200').count(), 1)

    def test_deactivates_only_missing_people_with_matching_source_binding_and_provider_label(self):
        """Would fail if a stale FK or other ownership were enough to deactivate a row."""
        sync = _sync_module()
        source = self.source()
        other_source = self.source(name='分部目录', source_key='feishu-branch')
        owned_missing = People.objects.create(
            employee_id='EMP-301', name='应停用', source='feishu', sync_source=source,
        )
        owned_seen = People.objects.create(
            employee_id='EMP-302', name='仍在目录', source='feishu', sync_source=source,
        )
        manual = People.objects.create(employee_id='EMP-303', name='手工', source='manual')
        csv_owned = People.objects.create(employee_id='EMP-304', name='CSV', source='csv')
        other_api = People.objects.create(
            employee_id='EMP-305', name='另一实例', source='feishu', sync_source=other_source,
        )
        stale_fk = People.objects.create(
            employee_id='EMP-306', name='已转 CSV', source='csv', sync_source=source,
        )

        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-302', name='仍在目录'),
        ]))
        self.assertEqual(preview.deactivations, ({'employee_id': 'EMP-301'},))
        sync.apply_people_sync(source, preview)

        owned_missing.refresh_from_db()
        owned_seen.refresh_from_db()
        manual.refresh_from_db()
        csv_owned.refresh_from_db()
        other_api.refresh_from_db()
        stale_fk.refresh_from_db()
        self.assertFalse(owned_missing.is_active)
        self.assertTrue(owned_seen.is_active)
        self.assertTrue(manual.is_active)
        self.assertTrue(csv_owned.is_active)
        self.assertTrue(other_api.is_active)
        self.assertTrue(stale_fk.is_active)

    def test_duplicate_remote_employee_number_is_a_validation_failure_that_cannot_apply(self):
        """Would fail if distinct platform users sharing an employee ID were picked silently."""
        sync = _sync_module()
        source = self.source()
        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-400', name='甲', external_user_id='ou-a'),
            DirectoryPerson(employee_id='EMP-400', name='乙', external_user_id='ou-b'),
        ]))

        self.assertFalse(preview.is_valid)
        self.assertFalse(preview.token)
        self.assertEqual(People.objects.count(), 0)
        with self.assertRaises(sync.PeopleSyncApplyError):
            sync.apply_people_sync(source, preview)
        self.assertEqual(People.objects.count(), 0)

    def test_file_imported_employee_is_adopted_by_api_source_using_employee_number(self):
        """Would fail if an earlier CSV import prevented switching to API synchronization."""
        sync = _sync_module()
        source = self.source()
        person = People.objects.create(
            employee_id='EMP-500', name='文件姓名', department='旧部门', source='csv',
        )

        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(
                employee_id='EMP-500', name='目录姓名', department='新部门',
                external_user_id='ou-500',
            ),
        ]))

        self.assertTrue(preview.is_valid)
        self.assertEqual([row['employee_id'] for row in preview.updates], ['EMP-500'])
        result = sync.apply_people_sync(source, preview)
        person.refresh_from_db()
        self.assertEqual((result.created, result.updated), (0, 1))
        self.assertEqual(
            (person.name, person.department, person.source, person.sync_source_id),
            ('目录姓名', '新部门', 'feishu', source.pk),
        )

    def test_employee_owned_by_another_api_source_never_transfers_ownership(self):
        """Would fail if a matching employee ID overwrote another API source."""
        sync = _sync_module()
        source = self.source()
        other_source = self.source(name='分部目录', source_key='feishu-branch')
        protected = People.objects.create(
            employee_id='EMP-501', name='分部姓名', source='feishu',
            sync_source=other_source,
        )
        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-501', name='总部姓名', external_user_id='ou-501'),
        ]))

        self.assertFalse(preview.is_valid)
        with self.assertRaises(sync.PeopleSyncApplyError):
            sync.apply_people_sync(source, preview)
        protected.refresh_from_db()
        self.assertEqual((protected.name, protected.source, protected.sync_source_id),
                         ('分部姓名', 'feishu', other_source.pk))

    def test_incomplete_or_wrong_source_snapshot_never_proposes_deactivations(self):
        """Would fail if an untrusted snapshot were interpreted as people leaving."""
        sync = _sync_module()
        source = self.source()
        owned = People.objects.create(employee_id='EMP-601', source='feishu', sync_source=source)
        for adapter in (
            SnapshotAdapter(source, [], source_key='another-source'),
            SnapshotAdapter(source, [], complete=False),
        ):
            with self.subTest(adapter_source_key=adapter.source_key, complete=adapter._complete):
                preview = sync.preview_people_sync(source, adapter)
                self.assertFalse(preview.is_valid)
                self.assertEqual(preview.deactivations, ())
                self.assertFalse(preview.token)
        owned.refresh_from_db()
        self.assertTrue(owned.is_active)

    def test_skipped_external_identity_protects_same_source_platform_user_from_deactivation(self):
        """Would fail if a missing employee number removed a known platform user."""
        sync = _sync_module()
        source = self.source()
        protected = People.objects.create(
            employee_id='EMP-701', source='feishu', sync_source=source,
            platform_user_id='ou-missing',
        )
        preview = sync.preview_people_sync(source, SnapshotAdapter(
            source,
            [DirectoryPerson(employee_id='EMP-702', name='其他人员', external_user_id='ou-702')],
            skipped_records=({'external_user_id': 'ou-missing', 'reason': 'missing_employee_id'},),
        ))

        self.assertTrue(preview.is_valid)
        self.assertEqual(preview.skipped, ({
            'external_user_id': 'ou-missing', 'reason': 'missing_employee_id',
        },))
        self.assertEqual(preview.deactivations, ())
        sync.apply_people_sync(source, preview)
        protected.refresh_from_db()
        self.assertTrue(protected.is_active)

    def test_preview_is_publicly_serializable_but_tampering_or_stale_state_blocks_apply_atomically(self):
        """Would fail if unsigned edits, changed source config, or local drift could be applied."""
        sync = _sync_module()
        source = self.source()
        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-801', name='原始', external_user_id='ou-801'),
        ]))

        public_preview = preview.to_dict()
        rendered = json.dumps(public_preview)
        self.assertNotIn('fixture-secret', rendered)
        self.assertNotIn('app_secret', rendered)
        tampered = replace(preview, creates=({
            **preview.creates[0], 'name': '篡改',
        },))
        with self.assertRaises(sync.PeopleSyncApplyError):
            sync.apply_people_sync(source, tampered)
        self.assertFalse(People.objects.filter(employee_id='EMP-801').exists())

        source.root_department_ids = ['changed-root']
        source.save(update_fields=['root_department_ids'])
        with self.assertRaises(sync.PeopleSyncApplyError):
            sync.apply_people_sync(source, preview)
        self.assertFalse(People.objects.filter(employee_id='EMP-801').exists())

    def test_invalid_field_and_concurrent_employee_collision_leave_no_partial_writes(self):
        """Would fail if bad model data or a late uniqueness collision partly committed a sync."""
        sync = _sync_module()
        source = self.source()
        invalid_preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-901', name='可以'),
            DirectoryPerson(employee_id='EMP-902', name='过长' * 200),
        ]))
        self.assertFalse(invalid_preview.is_valid)
        with self.assertRaises(sync.PeopleSyncApplyError):
            sync.apply_people_sync(source, invalid_preview)
        self.assertEqual(People.objects.count(), 0)

        collision_preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-903', name='先创建'),
            DirectoryPerson(employee_id='EMP-904', name='后碰撞'),
        ]))
        People.objects.create(employee_id='EMP-904', name='并发手工', source='manual')
        with self.assertRaises(sync.PeopleSyncApplyError):
            sync.apply_people_sync(source, collision_preview)
        self.assertFalse(People.objects.filter(employee_id='EMP-903').exists())
        self.assertEqual(People.objects.get(employee_id='EMP-904').source, 'manual')

    def test_preview_rejects_an_adapter_built_with_stale_source_configuration_before_fetch(self):
        """Would fail if a stale adapter could fetch under a relabelled source scope."""
        sync = _sync_module()
        source = self.source(root_department_ids=['old-root'])
        adapter = SnapshotAdapter(source, [DirectoryPerson(employee_id='EMP-1001')])
        source.root_department_ids = ['new-root']
        source.save(update_fields=['root_department_ids'])

        preview = sync.preview_people_sync(source, adapter)

        self.assertFalse(preview.is_valid)
        self.assertEqual(adapter.iter_calls, 0)
        self.assertFalse(preview.token)

    def test_preview_rejects_source_configuration_drift_while_adapter_fetches(self):
        """Would fail if a completed old-scope fetch were accepted after a config change."""
        sync = _sync_module()
        source = self.source(root_department_ids=['old-root'])

        def change_scope_during_fetch():
            source.root_department_ids = ['new-root']
            source.save(update_fields=['root_department_ids'])

        adapter = SnapshotAdapter(
            source, [DirectoryPerson(employee_id='EMP-1002')], before_iter=change_scope_during_fetch,
        )

        preview = sync.preview_people_sync(source, adapter)

        self.assertFalse(preview.is_valid)
        self.assertEqual(adapter.iter_calls, 1)
        self.assertFalse(preview.token)

    def test_credential_only_save_invalidates_an_existing_preview_without_exposing_credentials(self):
        """Would fail if update_fields(credentials) left a signed preview applicable."""
        sync = _sync_module()
        source = self.source()
        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-1101'),
        ]))
        source.credentials = {'app_id': 'new-fixture-app', 'app_secret': 'new-fixture-secret'}
        source.save(update_fields=['credentials'])

        with self.assertRaises(sync.PeopleSyncApplyError):
            sync.apply_people_sync(source, preview)
        self.assertFalse(People.objects.filter(employee_id='EMP-1101').exists())
        self.assertNotIn('new-fixture-secret', preview.token)
        self.assertNotIn('fixture-secret', preview.token)

    def test_successful_noop_consumes_preview_without_invalidating_connection_test(self):
        from django.utils import timezone
        sync = _sync_module()
        source = self.source()
        PeopleSyncSource.objects.filter(pk=source.pk).update(last_tested_at=timezone.now())
        source.refresh_from_db()
        configured_at = source.updated_at
        first = sync.preview_people_sync(source, SnapshotAdapter(source, []))
        parallel = sync.preview_people_sync(source, SnapshotAdapter(source, []))
        sync.apply_people_sync(source, first)
        source.refresh_from_db()
        self.assertEqual(source.updated_at, configured_at)
        self.assertTrue(source.public_data()['connection_test_current'])
        for stale in (first, parallel):
            with self.assertRaises(sync.PeopleSyncApplyError):
                sync.apply_people_sync(source, stale)
        fresh = sync.preview_people_sync(source, SnapshotAdapter(source, []))
        sync.apply_people_sync(source, fresh)

    def test_repeat_missing_inactive_person_is_a_noop_and_does_not_rewrite_person_timestamp(self):
        """Would fail if every complete omission re-deactivated an already inactive person."""
        sync = _sync_module()
        source = self.source()
        person = People.objects.create(employee_id='EMP-1201', source='feishu', sync_source=source)
        first = sync.apply_people_sync(source, sync.preview_people_sync(source, SnapshotAdapter(source, [])))
        person.refresh_from_db()
        first_timestamp = person.last_synced_at
        second_preview = sync.preview_people_sync(source, SnapshotAdapter(source, []))

        second = sync.apply_people_sync(source, second_preview)

        person.refresh_from_db()
        self.assertEqual(first.deactivated, 1)
        self.assertEqual(second_preview.deactivations, ())
        self.assertEqual(second.deactivated, 0)
        self.assertEqual(person.last_synced_at, first_timestamp)

    def test_dictionary_roundtrip_and_expired_preview_are_safe_apply_boundaries(self):
        """Would fail if a UI payload could not round-trip or an expired token were accepted."""
        sync = _sync_module()
        source = self.source()
        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-1301'),
        ]))

        result = sync.apply_people_sync(source, preview.to_dict())

        self.assertEqual(result.created, 1)
        with patch('django.core.signing.time.time', return_value=1):
            expired = sync.preview_people_sync(source, SnapshotAdapter(source, [
                DirectoryPerson(employee_id='EMP-1302'),
            ]))
        with self.assertRaises(sync.PeopleSyncApplyError):
            sync.apply_people_sync(source, expired.to_dict())
        self.assertFalse(People.objects.filter(employee_id='EMP-1302').exists())

    def test_database_failure_after_an_earlier_save_rolls_back_the_entire_apply(self):
        """Would fail if an exception on the second write left the first create committed."""
        sync = _sync_module()
        source = self.source()
        preview = sync.preview_people_sync(source, SnapshotAdapter(source, [
            DirectoryPerson(employee_id='EMP-1401'),
            DirectoryPerson(employee_id='EMP-1402'),
        ]))
        original_save = People.save
        save_count = 0

        def fail_second_save(instance, *args, **kwargs):
            nonlocal save_count
            save_count += 1
            if save_count == 2:
                raise IntegrityError('fixture database failure')
            return original_save(instance, *args, **kwargs)

        with patch.object(People, 'save', autospec=True, side_effect=fail_second_save):
            with self.assertRaises(sync.PeopleSyncApplyError):
                sync.apply_people_sync(source, preview)

        self.assertFalse(People.objects.filter(employee_id='EMP-1401').exists())
        self.assertFalse(People.objects.filter(employee_id='EMP-1402').exists())

    def test_sync_binds_a_frozen_concrete_adapter_snapshot_to_persisted_source_configuration(self):
        """Would fail if an in-memory mutation made Feishu fetch another scope under an old identity."""
        from net.people.directory.feishu import FeishuDirectoryAdapter

        sync = _sync_module()
        source = self.source(root_department_ids=['persisted-root'])
        protected = People.objects.create(
            employee_id='EMP-1501', source='feishu', sync_source=source,
        )
        fetched_roots = []

        def handler(method, url, kwargs):
            if url.endswith('/departments/persisted-root'):
                return ProviderResponse({'code': 0, 'data': {'department': {'name': '总部'}}})
            if url.endswith('/tenant_access_token/internal'):
                self.assertEqual(kwargs['json'], {
                    'app_id': 'fixture-app', 'app_secret': 'fixture-secret',
                })
                return ProviderResponse({'code': 0, 'tenant_access_token': 'tenant-token'})
            if url.endswith('/users/find_by_department'):
                fetched_roots.append(kwargs['params']['department_id'])
                return ProviderResponse({'code': 0, 'data': {
                    'items': [{'employee_no': 'EMP-1501', 'open_id': 'ou-1501'}],
                    'has_more': False, 'page_token': '',
                }})
            if '/children' in url:
                return ProviderResponse({'code': 0, 'data': {
                    'items': [], 'has_more': False, 'page_token': '',
                }})
            self.fail(f'unexpected fixture request: {method} {url}')

        adapter = FeishuDirectoryAdapter(source, session=ProviderSession(handler))
        source.root_department_ids[:] = ['unsaved-root']
        source.credentials['app_secret'] = 'unsaved-secret'
        source.is_enabled = False

        preview = sync.preview_people_sync(source, adapter)

        self.assertTrue(preview.is_valid)
        self.assertEqual(fetched_roots, ['persisted-root'])
        self.assertEqual(preview.deactivations, ())
        protected.refresh_from_db()
        self.assertTrue(protected.is_active)
        self.assertNotIn('unsaved-secret', preview.token)
