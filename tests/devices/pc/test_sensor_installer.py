"""Exercise driver installation control flow without running an installer."""
import subprocess
import tempfile
from pathlib import Path
from django.test import SimpleTestCase

class SensorInstallerTests(SimpleTestCase):
    def test_install_is_verified_idempotent_and_failures_do_not_claim_success(self):
        source=Path('net/scripts/templates/Install-PCCollector.ps1').read_text(encoding='utf-8-sig')
        functions=source[source.index('function Get-PCPawnIOVersion'):source.index('function Install-PCCollector {')]
        body=r"""
$ErrorActionPreference='Stop'
$root=$args[0]
$src=Join-Path $root 'source';$dest=Join-Path $root 'target'
[IO.Directory]::CreateDirectory($src)|Out-Null
[IO.Directory]::CreateDirectory($dest)|Out-Null
[IO.File]::WriteAllText((Join-Path $src 'PawnIO_setup.exe'),'fake installer - never execute')
$script:version=$null;$script:mode='ok';$script:calls=0
function Get-PCPawnIOVersion {return $script:version}
function Get-FileHash {param($LiteralPath,$Algorithm)
 if ($script:mode -eq 'hash') {return @{Hash='invalid'}}
 return @{Hash='1F519A22E47187F70A1379A48CA604981C4FCF694F4E65B734AAA74A9FBA3032'}
}
function Get-AuthenticodeSignature {param($LiteralPath)
 if ($script:mode -eq 'signature') {return @{Status='NotSigned'}}
 return @{Status='Valid'}
}
function Start-Process {param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru)
 $script:calls++
 if ($FilePath -notlike ($dest+'*') -or ($ArgumentList -join ' ') -ne '-install -silent' -or $WindowStyle -ne 'Hidden') {throw 'unsafe installer invocation'}
 $code=0
 if ($script:mode -eq 'exit') {$code=5}
 if ($script:mode -eq 'ok') {$script:version=[version]'2.2.0'}
 $process=[pscustomobject]@{ExitCode=$code;HasExited=$true}
 $process|Add-Member ScriptMethod WaitForExit {param($timeout) return $true}
 $process|Add-Member ScriptMethod Dispose {}
 return $process
}
Install-PCSensorDriver $src $dest
if ($script:calls -ne 1 -or @(Get-ChildItem $dest).Count) {throw 'install/cleanup failed'}
Install-PCSensorDriver $src $dest
if ($script:calls -ne 1) {throw 'driver installed twice'}
foreach ($mode in @('hash','signature','exit','registration')) {
 $script:version=$null;$script:mode=$mode;$before=$script:calls;$failed=$false
 try {Install-PCSensorDriver $src $dest} catch {$failed=$true}
 if (-not $failed -or @(Get-ChildItem $dest).Count) {throw 'failed installation claimed success or leaked temp file'}
 if ($mode -in @('hash','signature') -and $script:calls -ne $before) {throw 'untrusted installer executed'}
}
"""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'driver.ps1'
            path.write_text(functions+body,encoding='utf-8-sig')
            result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(path),directory],capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
