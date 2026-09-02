import codecs
from contextlib import redirect_stdout
from datetime import date
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from net.models import (
    ComputerAnalysisProfile,
    Domain_Controller_Config,
    Network_Device,
    Server,
    TaskRun,
)
from net.scripts.generator import generate_pc_script


def embedded_profile_config(result):
    if result.filename.endswith('.ps1'):
        prefix = "$ProfileConfig = @'\n"
        suffix = "\n'@ | ConvertFrom-Json"
    else:
        prefix = "PROFILE_CONFIG_JSON=$(cat <<'NET_PROFILE_CONFIG'\n"
        suffix = '\nNET_PROFILE_CONFIG\n)'
    return json.loads(result.content.split(prefix, 1)[1].split(suffix, 1)[0])


class PcScriptGeneratorTests(TestCase):
    def setUp(self):
        self.windows_profile = ComputerAnalysisProfile(
            name='Windows upload',
            scan_directories=[r'C:\InspectionLogs', r"C:\Managed Logs\O'Brien"],
            recursive=True,
            file_time_mode=ComputerAnalysisProfile.FileTimeMode.RECENT_DAYS,
            recent_days=14,
            analysis_items=['resource', 'activation'],
        )
        self.macos_profile = ComputerAnalysisProfile(
            name='macOS upload',
            scan_directories=['/Library/Logs/Inspection', '/Volumes/受管日志'],
            file_time_mode=ComputerAnalysisProfile.FileTimeMode.DATE_RANGE,
            recent_days=None,
            range_start_date=date(2026, 8, 1),
            range_end_date=date(2026, 8, 31),
            analysis_items=['resource'],
        )
        self.windows_profile.save()
        self.macos_profile.save()

    def test_windows_script_embeds_parseable_minimal_profile_configuration(self):
        result = generate_pc_script(
            self.windows_profile,
            'windows',
            'https://monitor.example/dashboard/?ignored=true',
        )

        self.assertEqual(result.filename, 'GetInfo_Upload.ps1')
        self.assertEqual(result.content_type, 'text/plain; charset=utf-8')
        self.assertEqual(embedded_profile_config(result), {
            'scan_directories': [r'C:\InspectionLogs', r"C:\Managed Logs\O'Brien"],
            'recursive': True,
            'file_time_mode': 'recent_days',
            'recent_days': 14,
            'analysis_items': ['resource', 'activation'],
            'upload_url': (
                'https://monitor.example/api/computer_inspection/'
                f'?profile_id={self.windows_profile.pk}'
            ),
        })

    def test_macos_script_embeds_parseable_date_range_configuration(self):
        result = generate_pc_script(self.macos_profile, 'macos', 'https://monitor.example')

        self.assertEqual(result.filename, 'getinfo_upload_macos.sh')
        self.assertEqual(result.content_type, 'text/plain; charset=utf-8')
        self.assertEqual(embedded_profile_config(result), {
            'scan_directories': ['/Library/Logs/Inspection', '/Volumes/受管日志'],
            'recursive': False,
            'file_time_mode': 'date_range',
            'range_start_date': '2026-08-01',
            'range_end_date': '2026-08-31',
            'analysis_items': ['resource'],
            'upload_url': (
                'https://monitor.example/api/computer_inspection/'
                f'?profile_id={self.macos_profile.pk}'
            ),
        })

    def test_windows_binary_content_has_utf8_bom_for_powershell_51(self):
        result = generate_pc_script(self.windows_profile, 'windows', 'https://monitor.example')

        self.assertIsInstance(result.content, str)
        self.assertEqual(result.encoding, 'utf-8-sig')
        self.assertTrue(result.as_bytes().startswith(codecs.BOM_UTF8))
        self.assertEqual(result.as_bytes().decode('utf-8-sig'), result.content)

    def test_macos_binary_content_is_plain_utf8(self):
        result = generate_pc_script(self.macos_profile, 'macos', 'https://monitor.example')

        self.assertEqual(result.encoding, 'utf-8')
        self.assertFalse(result.as_bytes().startswith(codecs.BOM_UTF8))
        self.assertEqual(result.as_bytes().decode('utf-8'), result.content)

    def test_only_explicit_profile_fields_are_rendered(self):
        self.windows_profile.bind_password = 'directory-password-private'
        self.windows_profile.api_token = 'api-token-private'
        self.windows_profile.device_password = 'device-password-private'

        result = generate_pc_script(self.windows_profile, 'windows', 'https://monitor.example')

        config = embedded_profile_config(result)
        self.assertEqual(set(config), {
            'scan_directories', 'recursive', 'file_time_mode', 'recent_days',
            'analysis_items', 'upload_url',
        })
        serialized_config = json.dumps(config, ensure_ascii=False)
        self.assertNotIn('directory-password-private', serialized_config)
        self.assertNotIn('api-token-private', serialized_config)
        self.assertNotIn('device-password-private', serialized_config)

    def test_generated_scripts_exclude_persisted_credentials_and_settings_secrets(self):
        """Expanding generator serialization beyond profile settings must never expose stored credentials."""
        Domain_Controller_Config.objects.create(
            host='directory.example.invalid',
            bind_username='DOMAIN\\script-reader',
            bind_password='domain-bind-password-private',
        )
        Network_Device.objects.create(
            device_name='secret-bearing-switch',
            ip='192.0.2.80',
            password='device-password-private',
        )
        Server.objects.create(
            name='secret-bearing-server',
            ip='192.0.2.81',
            password='server-password-private',
            api_token='device-api-token-private',
        )

        self.assertEqual(
            Domain_Controller_Config.objects.get().bind_password,
            'domain-bind-password-private',
        )
        self.assertTrue(Network_Device.objects.filter(password='device-password-private').exists())
        self.assertTrue(Server.objects.filter(api_token='device-api-token-private').exists())

        database_settings = {
            'default': {
                'ENGINE': 'django.db.backends.mysql',
                'NAME': 'net-production-private',
                'USER': 'net-db-user-private',
                'PASSWORD': 'net-db-password-private',
                'HOST': 'db.example.invalid',
                'PORT': '3306',
            },
        }
        with patch.object(settings, 'SECRET_KEY', 'django-secret-key-private'), patch.object(
            settings, 'DATABASES', database_settings,
        ):
            self.assertEqual(settings.SECRET_KEY, 'django-secret-key-private')
            self.assertEqual(settings.DATABASES['default']['PASSWORD'], 'net-db-password-private')
            rendered_scripts = '\n'.join(
                generate_pc_script(self.windows_profile, platform, 'https://monitor.example').content
                for platform in ('windows', 'macos')
            )
        sensitive_values = (
            'domain-bind-password-private',
            'device-password-private',
            'server-password-private',
            'device-api-token-private',
            'django-secret-key-private',
            'net-production-private',
            'net-db-user-private',
            'net-db-password-private',
            'db.example.invalid',
        )

        for secret in sensitive_values:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, rendered_scripts)

    def test_rejects_invalid_platform_path_and_public_url(self):
        with self.assertRaisesRegex(ValueError, 'platform'):
            generate_pc_script(self.windows_profile, 'linux', 'https://monitor.example')
        blank_paths = ComputerAnalysisProfile(name='blank paths', scan_directories=['   '])
        blank_paths.save()
        with self.assertRaisesRegex(ValueError, 'scan directory'):
            generate_pc_script(blank_paths, 'windows', 'https://monitor.example')
        with self.assertRaisesRegex(ValueError, 'HTTP'):
            generate_pc_script(self.windows_profile, 'windows', 'ftp://monitor.example')

    def test_rejects_public_urls_with_userinfo_or_invalid_ports(self):
        invalid_urls = (
            'https://user:password@monitor.example',
            'https://monitor.example:not-a-port',
            'https://monitor.example:70000',
            'https://monitor.example:0',
            'https://monitor.example:',
        )

        for public_url in invalid_urls:
            with self.subTest(public_url=public_url):
                with self.assertRaisesRegex(ValueError, 'public_base_url'):
                    generate_pc_script(self.windows_profile, 'windows', public_url)

    def test_rebuilds_ipv6_authority_with_validated_port(self):
        result = generate_pc_script(
            self.windows_profile,
            'windows',
            'https://[2001:db8::1]:8443/dashboard',
        )

        self.assertEqual(
            embedded_profile_config(result)['upload_url'],
            'https://[2001:db8::1]:8443/api/computer_inspection/'
            f'?profile_id={self.windows_profile.pk}',
        )

    def test_macos_script_contract_collects_real_inventory_and_usage_values(self):
        result = generate_pc_script(self.macos_profile, 'macos', 'https://monitor.example')

        self.assertIn('PYTHON3=$(command -v python3', result.content)
        self.assertNotIn('/usr/bin/command -v python3', result.content)
        for required in (
            "'当前登录用户工号'", "'当前登录用户姓名'", "'当前CPU占用率'",
            "'当前内存使用率'", "'磁盘总量'", "'MAC地址'", "'IP地址'",
            'id -un', 'id -F', r'\binet6?\s+', r'\bether\s+', 'CPU usage:',
            'vm_stat', 'Disk Size:', "-iname', '*.json'",
        ):
            with self.subTest(required=required):
                self.assertIn(required, result.content)

    def test_windows_script_contract_collects_usage_percentages_and_utf8_body(self):
        result = generate_pc_script(self.windows_profile, 'windows', 'https://monitor.example')

        for required in (
            'LoadPercentage', 'FreePhysicalMemory', "'当前CPU占用率'",
            "'当前内存使用率'", 'CultureInfo]::InvariantCulture',
            '[System.Text.Encoding]::UTF8.GetBytes',
        ):
            with self.subTest(required=required):
                self.assertIn(required, result.content)

    def test_macos_embedded_collector_extracts_inventory_and_case_insensitive_logs(self):
        result = generate_pc_script(self.macos_profile, 'macos', 'https://monitor.example')
        collector = result.content.split('  "$PYTHON3" - <<\'PY\'\n', 1)[1].split('\nPY\n', 1)[0]

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / 'captured.JSON'
            log_path.write_text('{}', encoding='utf-8')
            os.utime(log_path, (1786795200, 1786795200))

            def fake_find(command, **kwargs):
                root = Path(command[1])
                recursive = '-maxdepth' not in command
                paths = root.rglob('*') if recursive else root.iterdir()
                output = '\n'.join(str(path) for path in paths if path.is_file() and path.suffix.lower() == '.json')
                return SimpleNamespace(stdout=output)

            env = {
                'PROFILE_CONFIG_JSON': json.dumps({
                    'scan_directories': [directory], 'recursive': True,
                    'file_time_mode': 'date_range', 'range_start_date': '2026-08-01',
                    'range_end_date': '2026-08-31', 'upload_url': 'https://example.invalid',
                }),
                'SYSCTL_OUTPUT': '\n'.join((
                    'hw.physicalcpu: 4', 'hw.logicalcpu: 8', 'hw.memsize: 4096000',
                    'vm.page_size: 4096', 'machdep.cpu.brand_string: Apple M2',
                )),
                'CPU_USAGE_OUTPUT': 'CPU usage: 10.00% user, 5.00% sys, 85.00% idle',
                'VM_STAT_OUTPUT': 'Pages active: 100.\nPages wired down: 200.\nPages occupied by compressor: 300.',
                'DISKUTIL_OUTPUT': 'Disk Size: 500.0 GB (536870912000 Bytes)',
                'DF_OUTPUT': 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/disk3s1 1 1 1 1% /',
                'IFCONFIG_OUTPUT': 'en0: flags=8863<UP>\n\tether aa:bb:cc:dd:ee:ff\n\tinet 192.0.2.10 netmask 0xffffff00',
                'SYSTEM_PROFILER_OUTPUT': 'Hardware Overview:',
                'CURRENT_USER_ACCOUNT': 'alice', 'CURRENT_USER_FULL_NAME': 'Alice Example',
            }
            output = io.StringIO()
            with patch.dict(os.environ, env, clear=False), patch('subprocess.run', side_effect=fake_find), redirect_stdout(output):
                exec(collector, {'__name__': '__main__'})

        payload = json.loads(output.getvalue())
        self.assertEqual(payload['系统信息概览']['当前登录用户工号'], 'alice')
        self.assertEqual(payload['系统信息概览']['当前登录用户姓名'], 'Alice Example')
        self.assertEqual(payload['网络信息'], [{
            '接口名称': 'en0', 'IP地址': '192.0.2.10', 'MAC地址': 'aa:bb:cc:dd:ee:ff',
        }])
        self.assertEqual(payload['计算机硬件资源情况']['当前CPU占用率'], '15.0%')
        self.assertEqual(payload['计算机硬件资源情况']['当前内存使用率'], '60.0%')
        self.assertEqual(payload['计算机硬件资源情况']['磁盘总量'], '500.00GB')
        self.assertEqual([item['path'] for item in payload['日志文件元数据']], [str(log_path)])


class PcScriptUnsavedProfileTests(SimpleTestCase):
    def test_generate_rejects_unsaved_profile_with_clear_error(self):
        profile = ComputerAnalysisProfile(
            name='Unsaved profile', scan_directories=['C:/logs'], analysis_items=['resource'])

        with self.assertRaisesRegex(ValueError, 'must be saved to the database'):
            generate_pc_script(profile, 'windows', 'https://monitor.example')


class PcScriptUploadProfileTests(TestCase):
    def test_generated_upload_url_queues_selected_profile(self):
        ComputerAnalysisProfile.objects.create(
            name='Earlier fallback',
            scan_directories=['C:/fallback'],
            analysis_items=['activation'],
        )
        selected = ComputerAnalysisProfile.objects.create(
            name='Selected resource profile',
            scan_directories=['C:/selected'],
            analysis_items=['resource'],
        )
        result = generate_pc_script(selected, 'windows', 'https://monitor.example')
        upload_url = urlsplit(embedded_profile_config(result)['upload_url'])

        response = self.client.post(
            f'{upload_url.path}?{upload_url.query}',
            data=json.dumps({
                '日志时间': '2026-09-01 10:00:00',
                '系统信息概览': {'计算机名': 'PROFILE-BOUND-PC'},
                '网络信息': [],
                '计算机硬件资源情况': {
                    '当前CPU占用率': '12.5%',
                    '当前内存使用率': '34.0%',
                },
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(parse_qs(upload_url.query), {'profile_id': [str(selected.pk)]})
        task = TaskRun.objects.get()
        self.assertEqual(task.analysis_profile_id, selected.pk)
        self.assertEqual(task.profile_snapshot['analysis_items'], ['resource'])
