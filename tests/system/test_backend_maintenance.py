import hashlib
import importlib
import io
import json
import os
import runpy
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command, get_commands, CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from net.models import InspectionProfile, Server, Server_Inspection, TaskRun, TaskTargetRun


class SQLiteMaintenanceTests(SimpleTestCase):
    def helper(self):
        self.assertIsNotNone(importlib.util.find_spec('net.infrastructure.database'))
        return importlib.import_module('net.infrastructure.database')

    def test_timeout_accepts_finite_bounded_seconds(self):
        helper = self.helper()
        for value in ('0', '-1', '61', 'nan', 'inf', '', 'abc'):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                helper.sqlite_timeout(value)
        self.assertEqual(helper.sqlite_timeout('2.5'), 2.5)
        self.assertEqual(helper.sqlite_timeout('60'), 60)

    def test_settings_apply_timeout_without_changing_database_selection(self):
        with patch.dict(os.environ, {'DB_ENGINE': 'sqlite', 'DJANGO_SQLITE_PATH': 'chosen.sqlite3', 'NET_SQLITE_TIMEOUT': '12.5'}):
            values = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'net' / 'settings.py'))
            self.assertEqual(values['DATABASES']['default']['NAME'], 'chosen.sqlite3')
            self.assertEqual(values['DATABASES']['default']['OPTIONS']['timeout'], 12.5)
        with patch.dict(os.environ, {'DB_ENGINE': 'mysql', 'NET_SQLITE_TIMEOUT': 'invalid'}):
            values = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'net' / 'settings.py'))
            self.assertEqual(values['DATABASES']['default']['ENGINE'], 'django.db.backends.mysql')
            self.assertNotIn('timeout', values['DATABASES']['default']['OPTIONS'])

    @override_settings(NET_SQLITE_WAL_ENABLED=True)
    def test_wal_skips_memory_test_and_non_sqlite_connections(self):
        helper = self.helper()
        for vendor, name, argv in (
            ('mysql', 'net', ['manage.py', 'sqlite_wal']),
            ('sqlite', ':memory:', ['manage.py', 'sqlite_wal']),
            ('sqlite', 'file:memorydb_default?mode=memory&cache=shared', ['manage.py', 'sqlite_wal']),
            ('sqlite', 'test.sqlite3', ['manage.py', 'test']),
        ):
            connection = SimpleNamespace(vendor=vendor, settings_dict={'NAME': name})
            with self.subTest(name=name), patch('sys.argv', argv):
                self.assertEqual(helper.initialize_sqlite_wal(connection, apply=True)['status'], 'skipped')

    def test_wal_is_explicit_and_persistent_only_for_existing_file(self):
        helper = self.helper()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'isolated.sqlite3'
            raw = sqlite3.connect(path)
            connection = SimpleNamespace(
                vendor='sqlite', settings_dict={'NAME': path}, in_atomic_block=False,
                get_autocommit=lambda: True, cursor=raw.cursor,
            )
            try:
                with patch('sys.argv', ['manage.py', 'sqlite_wal']):
                    with override_settings(NET_SQLITE_WAL_ENABLED=False):
                        with self.assertRaises(CommandError):
                            helper.initialize_sqlite_wal(connection, apply=True)
                    with override_settings(NET_SQLITE_WAL_ENABLED=True):
                        self.assertEqual(helper.initialize_sqlite_wal(connection)['journal_mode'], 'delete')
                        self.assertEqual(helper.initialize_sqlite_wal(connection, apply=True)['journal_mode'], 'wal')
                self.assertEqual(raw.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            finally:
                raw.close()


class ResultArchiveTests(TestCase):
    def setUp(self):
        self.cutoff = timezone.now() - timedelta(days=30)
        self.old = self.cutoff - timedelta(days=1)
        self.profile = InspectionProfile.objects.create(name='maintenance', device_type='server')

    def target(self, *, status='success', task_status='success', finished=None, task_finished=None):
        task = TaskRun.objects.create(
            task_type='inspection', inspection_profile=self.profile, status=task_status,
            finished_at=None if task_status == 'running' else (task_finished or self.old),
            progress=100 if task_status == 'success' else 0,
            started_at=self.old - timedelta(hours=1), worker_id='fixture',
            lease_expires_at=timezone.now() if task_status == 'running' else None,
        )
        return TaskTargetRun.objects.create(
            task=task, target_type='server', target_id='server-a', status=status,
            finished_at=finished or self.old, result_type='server_inspection',
            result_id='historical-evidence', result_snapshot={'cpu': 42, 'text': '历史\n证据'},
        )

    def run_archive(self, **kwargs):
        self.assertIn('archive_task_results', get_commands())
        output = io.StringIO()
        call_command('archive_task_results', before=self.cutoff.isoformat(), limit=20,
                     stdout=output, **kwargs)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_preview_excludes_active_recent_boundary_and_missing_finish(self):
        eligible = self.target()
        self.target(status='running')
        self.target(task_status='running')
        self.target(finished=self.cutoff)
        self.target(task_finished=timezone.now())
        missing = self.target()
        TaskTargetRun.objects.filter(pk=missing.pk).update(finished_at=None)
        mixed = self.target()
        TaskTargetRun.objects.create(task=mixed.task, target_type='server', target_id='active')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'unused.jsonl'
            rows = self.run_archive(output=str(output))
            self.assertFalse(output.exists())
        candidates = [row for row in rows if row['kind'] == 'candidate']
        self.assertEqual([row['target_id'] for row in candidates], [str(eligible.pk)])
        self.assertNotIn('result_snapshot', candidates[0])

    def test_archive_roundtrips_snapshot_and_protected_evidence_without_db_writes(self):
        target = self.target()
        record = Server_Inspection.objects.create(
            server=Server.objects.create(ip='192.0.2.44'), task_target=target, details={'cpu': 42},
        )
        before = list(TaskTargetRun.objects.values())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'archive.jsonl'
            preview = self.run_archive()
            candidate = next(row for row in preview if row['kind'] == 'candidate')
            self.assertTrue(any(ref['model'] == 'net.Server_Inspection' for ref in candidate['protected_references']))
            self.run_archive(apply=True, output=str(output))
            lines = output.read_bytes().splitlines(keepends=True)
            archive = [json.loads(line) for line in lines]
            self.assertEqual(archive[0]['kind'], 'manifest')
            self.assertEqual(archive[1]['target']['result_snapshot'], {'cpu': 42, 'text': '历史\n证据'})
            self.assertEqual(archive[1]['task']['id'], str(target.task_id))
            self.assertEqual(archive[-1]['count'], 1)
            self.assertEqual(archive[-1]['sha256'], hashlib.sha256(b''.join(lines[:-1])).hexdigest())
            with self.assertRaises(CommandError):
                self.run_archive(apply=True, output=str(output))
            self.assertEqual(output.read_bytes(), b''.join(lines))
        self.assertEqual(list(TaskTargetRun.objects.values()), before)
        record.refresh_from_db()
        self.assertEqual(record.details, {'cpu': 42})

    def test_invalid_bounds_and_missing_output_are_rejected(self):
        self.assertIn('archive_task_results', get_commands())
        for options in (
            {'before': 'bad', 'limit': 1},
            {'before': '2020-01-01', 'limit': 1},
            {'before': (timezone.now() + timedelta(days=1)).isoformat(), 'limit': 1},
            {'before': self.cutoff.isoformat(), 'limit': 0},
            {'before': self.cutoff.isoformat(), 'limit': 1001},
            {'before': self.cutoff.isoformat(), 'limit': 1, 'apply': True},
        ):
            with self.subTest(options=options), self.assertRaises(CommandError):
                call_command('archive_task_results', stdout=io.StringIO(), **options)

    def test_limit_is_stable_and_archive_failure_is_not_reported_as_complete(self):
        self.assertIn('archive_task_results', get_commands())
        self.target()
        self.target()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'limited.jsonl'
            call_command('archive_task_results', before=self.cutoff.isoformat(), limit=1,
                         apply=True, output=str(output), stdout=io.StringIO())
            self.assertEqual(json.loads(output.read_text(encoding='utf-8').splitlines()[-1])['count'], 1)
            failed = Path(directory) / 'failed.jsonl'
            with patch('os.fsync', side_effect=OSError('disk error')), self.assertRaises(CommandError):
                self.run_archive(apply=True, output=str(failed))

    def compactable_target(self):
        target = self.target()
        record = Server_Inspection.objects.create(
            server=Server.objects.get_or_create(ip='192.0.2.55')[0], task_target=target,
            details={'payload': 'evidence' * 100}, summary='record summary',
        )
        original = {'details': record.details, 'summary': 'original summary',
                    'status': 'partial', 'health_status': 'abnormal', 'custom_marker': {'x': 1}}
        TaskTargetRun.objects.filter(pk=target.pk).update(result_id=str(record.pk), result_snapshot=original)
        target.refresh_from_db()
        return target, record, original

    def test_compact_requires_durable_archive_and_preserves_markers(self):
        target, record, original = self.compactable_target()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'compact.jsonl'
            self.run_archive(compact=True)
            target.refresh_from_db()
            self.assertEqual(target.result_snapshot, original)
            self.run_archive(compact=True, apply=True, output=str(output))
            archived = json.loads(output.read_text(encoding='utf-8').splitlines()[1])
            self.assertEqual(archived['target']['result_snapshot'], original)
        target.refresh_from_db()
        self.assertNotIn('details', target.result_snapshot)
        self.assertEqual(target.result_snapshot['snapshot_version'], 2)
        for key in ('summary', 'status', 'health_status', 'custom_marker'):
            self.assertEqual(target.result_snapshot[key], original[key])
        record.refresh_from_db()
        self.assertEqual(record.details, original['details'])

    def test_compaction_skips_missing_mismatched_and_changed_evidence(self):
        missing = self.target()
        target, record, original = self.compactable_target()
        Server_Inspection.objects.filter(pk=record.pk).update(details={'different': True})
        with tempfile.TemporaryDirectory() as directory:
            self.run_archive(compact=True, apply=True, output=str(Path(directory) / 'blocked.jsonl'))
        missing.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(target.result_snapshot, original)
        self.assertEqual(missing.result_snapshot['cpu'], 42)

    def test_fsync_failure_prevents_any_compaction(self):
        target, record, original = self.compactable_target()
        with tempfile.TemporaryDirectory() as directory:
            with patch('os.fsync', side_effect=OSError('disk error')), self.assertRaises(CommandError):
                self.run_archive(compact=True, apply=True, output=str(Path(directory) / 'failed.jsonl'))
        target.refresh_from_db()
        self.assertEqual(target.result_snapshot, original)

    def test_completion_marker_fsync_failure_prevents_compaction(self):
        target, record, original = self.compactable_target()
        with tempfile.TemporaryDirectory() as directory:
            with patch('os.fsync', side_effect=[None, OSError('completion sync failed')]), self.assertRaises(CommandError):
                self.run_archive(compact=True, apply=True, output=str(Path(directory) / 'failed.jsonl'))
        target.refresh_from_db()
        self.assertEqual(target.result_snapshot, original)

    def test_compaction_preserves_absent_status_health_and_legacy_values(self):
        for markers in ({'status': 'failed'}, {'health_status': 'abnormal'}, {},
                        {'status': None, 'health_status': None}):
            with self.subTest(markers=markers):
                target, record, original = self.compactable_target()
                original = {'details': record.details, **markers}
                TaskTargetRun.objects.filter(pk=target.pk).update(result_snapshot=original)
                with tempfile.TemporaryDirectory() as directory:
                    self.run_archive(compact=True, apply=True, output=str(Path(directory) / 'legacy.jsonl'))
                target.refresh_from_db()
                self.assertEqual(target.result_snapshot['snapshot_version'], 2)
                for key in ('status', 'health_status'):
                    self.assertEqual(key in target.result_snapshot, key in original)
                    if key in original:
                        self.assertEqual(target.result_snapshot[key], original[key])

    def test_compare_and_swap_preserves_concurrent_snapshot_update(self):
        from net.management.commands.archive_task_results import compaction_plan
        target, record, original = self.compactable_target()
        changed = {**original, 'concurrent_marker': True}

        def concurrent_change(current, **kwargs):
            plan = compaction_plan(current, **kwargs)
            TaskTargetRun.objects.filter(pk=current.pk).update(result_snapshot=changed)
            return plan

        with tempfile.TemporaryDirectory() as directory:
            with patch('net.management.commands.archive_task_results.compaction_plan', side_effect=concurrent_change):
                rows = self.run_archive(compact=True, apply=True, output=str(Path(directory) / 'race.jsonl'))
        target.refresh_from_db()
        self.assertEqual(target.result_snapshot, changed)
        self.assertEqual(rows[-1]['compacted'], 0)

    def test_record_owned_by_another_target_is_never_compacted(self):
        target, record, original = self.compactable_target()
        other = self.target()
        Server_Inspection.objects.filter(pk=record.pk).update(task_target=other)
        with tempfile.TemporaryDirectory() as directory:
            rows = self.run_archive(compact=True, apply=True, output=str(Path(directory) / 'wrong-owner.jsonl'))
        target.refresh_from_db()
        self.assertEqual(target.result_snapshot, original)
        self.assertEqual(rows[-1]['compacted'], 0)

    def test_cursor_advances_equal_timestamp_batches_without_duplicates(self):
        targets = [self.target(), self.target(), self.target()]
        seen, cursor = [], {}
        for _ in range(4):
            output = io.StringIO()
            call_command('archive_task_results', before=self.cutoff.isoformat(), limit=1, stdout=output, **cursor)
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            seen.extend(row['target_id'] for row in rows if row['kind'] == 'candidate')
            if rows[-1]['next_cursor']:
                cursor = rows[-1]['next_cursor']
        self.assertEqual(seen, sorted(str(target.pk) for target in targets))
