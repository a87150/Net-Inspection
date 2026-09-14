"""Contracts for daily shared-folder collectors; all writes use temporary local folders."""
import base64
import codecs
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase
from net.models import ComputerAnalysisProfile, PCLogSourceConfig
from net.scripts.generator import generate_pc_script

FIXTURES = Path(__file__).with_name('fixtures')
WINDOWS_SECTIONS = {
    '日志时间', 'platform', '系统信息概览', '网络信息', '计算机硬件资源情况',
    'Windows激活信息', 'KMS服务器连通情况', '当前与域服务器通讯情况',
    '已安装软件列表', '当前运行进程清单', 'BitLocker状态', 'WindowsDefender状态',
    '系统更新历史', '已应用策略', '浏览器插件情况', '计算机和用户匹配情况', '事件发现',
}

def config_of(script):
    encoded = script.content.split('# PC_CONFIG: ', 1)[1].splitlines()[0]
    return json.loads(base64.b64decode(encoded))

class PcScriptGeneratorTests(SimpleTestCase):
    def setUp(self):
        self.profile = ComputerAnalysisProfile(
            name='Daily', analysis_items=['resource'])
        self.profile._state.adding = False
        self.source = PCLogSourceConfig(
            source_type='ftp', host='worker-private.invalid', port=21,
            username='worker-secret', remote_incoming_directory='incoming',
            local_staging_directory='worker-private-stage', file_time_mode='recent_days',
            terminal_windows_path=r"\\files\incoming\O'Brien",
            terminal_macos_path="/Volumes/Logs/O'Brien")
        self.source._state.adding = False

    def test_paths_are_allowlisted_and_worker_transport_is_independent(self):
        self.source.password = 'worker-secret'
        for platform, destination in [('windows', self.source.terminal_windows_path),
                                      ('macos', self.source.terminal_macos_path)]:
            result = generate_pc_script(self.profile, self.source, platform)
            self.assertEqual(config_of(result)['destination'], destination)
            for forbidden in ('http', 'password', '/api/', 'worker-secret', 'worker-private',
                              'Invoke-RestMethod', 'Invoke-WebRequest', 'LDAP', '/ato'):
                self.assertNotIn(forbidden.lower(), result.content.lower())
            self.assertEqual(result.as_bytes().startswith(codecs.BOM_UTF8), platform == 'windows')

    def test_windows_preview_does_not_publish_or_write_daily_marker(self):
        script = generate_pc_script(self.profile, self.source, 'windows').content
        functions, entry = script.split('# Collection entry point', 1)
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / 'preview.ps1'
            harness.write_text(functions + "\nfunction Get-PCPayload { return @{ preview = 'ok' } }\n"
                               + "function Publish-PCDaily { throw 'Preview attempted to publish' }\n" + entry,
                               encoding='utf-8-sig')
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File', str(harness), '-Preview'], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            self.assertEqual(json.loads(result.stdout), {'preview': 'ok'})
            self.assertEqual(list(Path(directory).iterdir()), [harness])

    def test_saved_objects_and_platform_destination_are_required(self):
        for profile, source, platform in [
            (ComputerAnalysisProfile(), self.source, 'windows'),
            (self.profile, PCLogSourceConfig(), 'windows'),
            (self.profile, None, 'windows'),
            (self.profile, self.source, 'linux'),
        ]:
            with self.subTest(platform=platform), self.assertRaises(ValueError):
                generate_pc_script(profile, source, platform)
        for platform, field in [('windows', 'terminal_windows_path'), ('macos', 'terminal_macos_path')]:
            for value in ('', 'relative/path', 'https://example.invalid/logs', 'bad\npath'):
                setattr(self.source, field, value)
                with self.subTest(value=value), self.assertRaises(ValueError):
                    generate_pc_script(self.profile, self.source, platform)

    def test_config_cannot_break_script_delimiters(self):
        self.source.terminal_macos_path = "/Volumes/logs/'\nNET_PROFILE_CONFIG\n$(touch bad)"
        with self.assertRaises(ValueError):
            generate_pc_script(self.profile, self.source, 'macos')

    def test_public_kms_targets_are_configured_without_worker_connection_data(self):
        self.profile.kms_servers = ['kms.example.invalid']
        config = config_of(generate_pc_script(self.profile, self.source, 'windows'))
        self.assertEqual(config['kms_servers'], ['kms.example.invalid'])
        self.assertEqual(set(config), {'destination', 'kms_servers'})
        self.assertNotIn('host', config)
        self.assertNotIn('username', config)

    def test_fixtures_cover_analysis_schema(self):
        windows = json.loads((FIXTURES / 'terminal_log_windows.json').read_text(encoding='utf-8'))
        self.assertTrue(WINDOWS_SECTIONS <= windows.keys())
        self.assertEqual(windows['platform'], 'windows')
        self.assertIsInstance(windows['当前运行进程清单'][0]['进程名'], str)
        self.assertEqual(windows['已应用策略']['用户策略'], ['Example User Policy'])
        macos = json.loads((FIXTURES / 'terminal_log_macos.json').read_text(encoding='utf-8'))
        self.assertEqual(macos['platform'], 'macos')
        self.assertNotIn('Windows激活信息', macos)

    def test_windows_logged_in_user_policies_and_unavailable_query(self):
        script = generate_pc_script(self.profile, self.source, 'windows').content
        functions = script.split('# Collection entry point', 1)[0]
        policy_block = script.split("$payload['已应用策略'] =", 1)[1].split(
            "$payload['浏览器插件情况']", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / 'policies.ps1'
            harness.write_text(functions + r"""
$script:mode = 'present'
$script:lastPath = $null
function Get-CimInstance { [pscustomobject]@{ name = 'Example Computer Policy' } }
function gpresult.exe {
    if (($args -join '|') -notlike '/USER|EXAMPLE\tester|/SCOPE|USER|/X|*|/F') {
        throw 'wrong logged-in user or query scope'
    }
    $script:lastPath = $args[5]
    $global:LASTEXITCODE = 0
    $xml = '<Rsop xmlns="urn:rsop"><UserResults><GPO><Name>Example User Policy</Name></GPO></UserResults></Rsop>'
    switch ($script:mode) {
        'failed' { $global:LASTEXITCODE = 1 }
        'missing' { $xml = '<Rsop />' }
        'malformed' { $xml = '<' }
        'empty' { $xml = '<Rsop><UserResults /></Rsop>' }
        'nologin' { throw 'must not query when no logged-in user' }
    }
    [IO.File]::WriteAllText($script:lastPath, $xml)
}
$computerSystem = [pscustomobject]@{ UserName = 'EXAMPLE\tester' }
function Read-TestPolicies {
    """ + policy_block + r"""
}
$policies = Read-TestPolicies
if (@($policies['用户策略']).Count -ne 1 -or $policies['用户策略'][0] -ne 'Example User Policy') {
    throw 'logged-in user policies not collected'
}
foreach ($mode in @('failed', 'missing', 'malformed', 'empty', 'nologin')) {
    $script:mode = $mode
    if ($mode -eq 'nologin') { $computerSystem.UserName = $null }
    $policies = Read-TestPolicies
    if ($policies['计算机策略'][0] -ne 'Example Computer Policy') { throw 'computer policy lost' }
    if ($mode -eq 'empty') {
        if ($null -eq $policies['用户策略'] -or $policies['用户策略'].Count -ne 0) { throw 'empty query not preserved' }
    } elseif ($null -ne $policies['用户策略']) { throw 'unavailable must be null' }
    if ($script:lastPath -and (Test-Path -LiteralPath $script:lastPath)) { throw 'temporary report leaked' }
}
""", encoding='utf-8-sig')
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File',
                                     str(harness)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_windows_publisher_daily_marker_survives_worker_move_and_failure_retries(self):
        script = generate_pc_script(self.profile, self.source, 'windows').content
        functions = script.split('# Collection entry point', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / 'test.ps1'
            harness.write_text(functions + """
$root = $args[0]
$dest = Join-Path $root 'share'
$state = Join-Path $root 'state'
New-Item -ItemType Directory -Path $dest | Out-Null
$collect = { @{ 'platform' = 'windows'; '日志时间' = '2026-09-07 10:00:00' } }
Publish-PCDaily $dest $state 'TEST-PC' $collect
$file = Get-ChildItem -LiteralPath $dest -Filter '*.json'
if (@($file).Count -ne 1 -or $file.Name -notmatch '^TEST-PC-\\d{8}\\.json$') { throw 'daily filename' }
Remove-Item -LiteralPath $file.FullName
Publish-PCDaily $dest $state 'TEST-PC' { throw 'must skip collection' }
if (@(Get-ChildItem -LiteralPath $dest).Count -ne 0) { throw 'republished' }
$state2 = Join-Path $root 'retry-state'
try { Publish-PCDaily (Join-Path $root 'missing') $state2 'TEST-PC' $collect } catch {}
Publish-PCDaily $dest $state2 'TEST-PC' $collect
if (@(Get-ChildItem -LiteralPath $dest -Filter '*.json').Count -ne 1) { throw 'retry failed' }
if (@(Get-ChildItem -LiteralPath $dest -Filter '*.uploading').Count) { throw 'partial left' }
function Get-CimInstance {
    param($ClassName, $Filter)
    [pscustomobject]@{
        Name='Example CPU'; UserName='tester'; TotalPhysicalMemory=16384
        FreePhysicalMemory=8; NumberOfCores=4; NumberOfLogicalProcessors=8
        LoadPercentage=15; CurrentClockSpeed=2400; LastBootUpTime=[datetime]'2026-09-07'
        InstallDate=[datetime]'2026-01-01'; Caption='Windows'; Version='10.0'
        OSArchitecture='64-bit'; Size=1073741824; FreeSpace=536870912
        DeviceID='C:'; IPAddress=@('192.0.2.1'); MACAddress='02:00:00:00:00:01'
    }
}
function Read-Optional { param([scriptblock]$Read, [string]$Section) return $null }
function Get-PCInstalledSoftware { return ,@() }
function Get-PCCpuTemperature { return $null }
function Get-PCUserPolicies { param([string]$LoggedInUser) return $null }
$payload = Get-PCPayload
if ($payload.platform -ne 'windows') { throw 'platform marker' }
if ($payload['磁盘空间情况'][0].total_bytes -ne 1073741824 -or $payload['磁盘空间情况'][0].free_bytes -ne 536870912) { throw 'disk capacity/free evidence missing' }
if ($payload['计算机硬件资源情况']['当前CPU占用率'] -ne '15.0%') { throw 'cpu percentage' }
if ($null -ne $payload['Windows激活信息']) { throw 'optional unknown' }
""", encoding='utf-8-sig')
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File',
                                     str(harness), directory], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_macos_publisher_daily_marker_survives_worker_move_and_failure_retries(self):
        result = generate_pc_script(self.profile, self.source, 'macos')
        code = result.content.split("<<'PC_COLLECTOR'\n", 1)[1].split('\nPC_COLLECTOR', 1)[0]
        namespace = {'__name__': 'collector_test'}
        exec(compile(code, '<macos collector>', 'exec'), namespace)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'share'
            destination.mkdir()
            state = root / 'state'
            payload = {'platform': 'macos', '日志时间': '2026-09-07 10:00:00'}
            publish = namespace['publish_daily']
            publish(destination, state, 'TEST-MAC', lambda: payload)
            files = list(destination.glob('*.json'))
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r'^TEST-MAC-\d{8}\.json$')
            self.assertEqual(json.loads(files[0].read_text(encoding='utf-8')), payload)
            files[0].unlink()
            def must_skip():
                self.fail('collected twice after Worker move')
            publish(destination, state, 'TEST-MAC', must_skip)
            self.assertEqual(list(destination.iterdir()), [])
            with self.assertRaises(OSError):
                publish(root / 'missing', root / 'retry', 'TEST-MAC', lambda: payload)
            publish(destination, root / 'retry', 'TEST-MAC', lambda: payload)
            self.assertEqual(len(list(destination.glob('*.json'))), 1)
            self.assertEqual(list(destination.glob('*.uploading')), [])

    def test_macos_collection_keeps_unavailable_memory_unknown(self):
        result = generate_pc_script(self.profile, self.source, 'macos')
        code = result.content.split("<<'PC_COLLECTOR'\n", 1)[1].split('\nPC_COLLECTOR', 1)[0]
        namespace = {'__name__': 'collector_test'}
        exec(compile(code, '<macos collector>', 'exec'), namespace)
        def read(*args):
            if args[0] == '/usr/sbin/sysctl':
                return 'hw.memsize: 16384\nvm.page_size: 4096\nhw.physicalcpu: 4\nhw.logicalcpu: 8'
            if args[0] == '/sbin/ifconfig':
                return 'en0: flags=1\n ether 02:00:00:00:00:01\n inet 192.0.2.1'
            if args[0] == '/usr/bin/top':
                return 'CPU usage: 10.0% user, 5.0% sys'
            return ''
        namespace['read_command'] = read
        payload = namespace['collect_payload'](config_of(result))
        self.assertEqual(payload['platform'], 'macos')
        self.assertEqual(payload['计算机硬件资源情况']['当前CPU占用率'], '15.0%')
        self.assertIsNone(payload['计算机硬件资源情况']['当前内存使用率'])
        self.assertEqual(payload['网络信息'][0]['IP地址'], '192.0.2.1')
        self.assertNotIn('Windows激活信息', payload)
