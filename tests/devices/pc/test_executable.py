import json
import os
from pathlib import Path
import subprocess
import tempfile
import hashlib
from unittest import skipUnless
from unittest import TestCase
from net.scripts.sensors import sensor_bundle
from net.scripts.executable import package_collector


class CollectorPackageTests(TestCase):
    def test_prebuilt_host_carries_verified_script_and_library_without_compiling(self):
        host = b'MZ-prebuilt-host'
        script = b'Write-Output preview'
        library = b'fixture-library'
        package = package_collector(host, script, library)

        self.assertTrue(package.startswith(host))
        self.assertEqual(package[-48:-40], b'PCCOLV02')
        self.assertEqual(int.from_bytes(package[-40:-32], 'little'), len(package) - len(host) - 48)
        self.assertEqual(package[-32:], hashlib.sha256(package[len(host):-48]).digest())
        self.assertIn(script, package)
        self.assertIn(library, package)


@skipUnless(os.name == 'nt', 'Windows PowerShell runtime required')
class ExecutableTests(TestCase):
    def run_host(self, path, *arguments):
        try:
            return subprocess.run([str(path), *arguments], capture_output=True, timeout=30)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 225:
                self.skipTest('Windows security blocked the generated test executable (WinError 225); protection was left enabled')
            raise

    def test_real_host_preserves_preview_args_and_child_failure_without_collection(self):
        source = """[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'LibreHardwareMonitorLib.dll'))) { throw 'DLL missing' }
if ($args -contains '-Preview') { @{preview='ok';runtime=$PSVersionTable.PSEdition} | ConvertTo-Json -Compress; exit 0 }
if ($args -contains '-PackageSelfTest') { @{self_test='ok';runtime=$PSVersionTable.PSEdition} | ConvertTo-Json -Compress; exit 0 }
[Console]::Error.WriteLine('fixture failure'); exit 23
"""
        from net.scripts.executable import package_collector, prebuilt_host
        exe = package_collector(prebuilt_host(), source.encode('utf-8-sig'),
                                sensor_bundle())
        with tempfile.TemporaryDirectory(prefix='pc-host-test-') as directory:
            path = Path(directory) / 'collector with space.exe'
            path.write_bytes(exe)
            result = self.run_host(path, '-Preview')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'preview':'ok', 'runtime':'Desktop'})
            result = self.run_host(path, '--self-test')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'self_test':'ok', 'runtime':'Desktop'})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_generated_collector_self_test_has_no_powershell_encoding_error(self):
        from types import SimpleNamespace
        from net.scripts.generator import generate_pc_script
        from net.scripts.executable import prebuilt_host
        saved = SimpleNamespace(adding=False)
        profile = SimpleNamespace(_state=saved, pk=1, kms_servers=[])
        origin = SimpleNamespace(_state=saved, pk=1, endpoint_url='https://monitor.example.invalid/api/pc/logs/', get_token=lambda: 'test-token-with-at-least-thirty-two-characters')
        script = generate_pc_script(profile, origin, 'windows')
        library = sensor_bundle()
        with tempfile.TemporaryDirectory(prefix='pc-generated-self-test-') as directory:
            path = Path(directory) / 'PCCollector.exe'
            path.write_bytes(package_collector(prebuilt_host(), script.as_bytes(), library))
            result = self.run_host(path, '--self-test')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b'')
            self.assertEqual(json.loads(result.stdout)['status'], 'ok')


    def test_host_rejects_dependency_path_traversal(self):
        import io
        import zipfile
        from net.scripts.executable import prebuilt_host
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('../outside.dll', b'invalid')
        package = package_collector(prebuilt_host(), b'throw "must not execute"', bundle.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.exe'
            path.write_bytes(package)
            result = self.run_host(path, '--self-test')
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'Invalid sensor dependency path', result.stderr)
