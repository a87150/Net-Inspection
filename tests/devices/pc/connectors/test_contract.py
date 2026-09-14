from datetime import date, datetime, timezone
from types import SimpleNamespace
from django.test import SimpleTestCase, override_settings
from net.devices.pc.connectors.base import RemoteLogEntry, normalize_path, select_entries


def source(**kwargs):
    values = dict(host='files.example', port=21, username='collector', domain='', share_name='',
                  remote_root_directory='/logs', remote_incoming_directory='incoming',
                  remote_processed_directory='incoming/processed', remote_failed_directory='failed',
                  recursive=True, file_time_mode='recent_days', recent_days=2,
                  range_start_date=None, range_end_date=None, ftp_passive=True, ftp_use_tls=False)
    values.update(kwargs)
    return SimpleNamespace(**values)


def entry(path, day=7, size=2):
    return RemoteLogEntry(path, size, datetime(2026, 9, day, 4, tzinfo=timezone.utc))


@override_settings(TIME_ZONE='Asia/Shanghai')
class ContractTests(SimpleTestCase):
    def test_rejects_escape_and_command_injection(self):
        for path in ('../x', '/absolute', 'a/../x', 'C:/x', 'x\r\nDELE y'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_path(path)

    def test_filters_old_temporary_archive_and_outside_files(self):
        rows = [entry('incoming/current.json'), entry('incoming/previous.JSON', 6),
                entry('incoming/old.json', 5), entry('incoming/x.json.uploading'),
                entry('incoming/processed/old.json'), entry('other/file.json')]
        self.assertEqual([r.path for r in select_entries(source(), rows, now=entry('now').modified_at)],
                         ['incoming/current.json', 'incoming/previous.JSON'])

    def test_range_uses_local_calendar_dates(self):
        config = source(file_time_mode='date_range', recent_days=None,
                        range_start_date=date(2026, 9, 6), range_end_date=date(2026, 9, 6))
        rows = [RemoteLogEntry('incoming/local.json', 2,
                              datetime(2026, 9, 5, 17, tzinfo=timezone.utc)), entry('incoming/no.json')]
        self.assertEqual([r.path for r in select_entries(config, rows)], ['incoming/local.json'])
