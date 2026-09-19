"""Execute sensor selection with fake hardware; never load a kernel driver."""
import subprocess
import tempfile
from pathlib import Path
from django.test import SimpleTestCase

class TemperatureTests(SimpleTestCase):
    def run_script(self, body):
        source = Path('net/scripts/templates/GetInfo_Upload.ps1').read_text(encoding='utf-8-sig')
        functions = source[source.index('function Publish-PCDaily'):source.index('# Collection entry point')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sensors.ps1'
            path.write_text("$ErrorActionPreference='Stop'\n" + functions + body, encoding='utf-8-sig')
            result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File', str(path)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_bundled_monitor_enables_cpu_uses_maximum_and_closes(self):
        self.run_script(r"""
$script:closed = $false
$hardware = [pscustomobject]@{ HardwareType='Cpu'; Sensors=@(
 [pscustomobject]@{SensorType='Temperature'; Value=44},
 [pscustomobject]@{SensorType='Temperature'; Value=55},
 [pscustomobject]@{SensorType='Temperature'; Value=200}); SubHardware=@() }
$hardware | Add-Member ScriptMethod Update {}
$script:fakeMonitor = [pscustomobject]@{IsCpuEnabled=$false; Hardware=@($hardware)}
$script:fakeMonitor | Add-Member ScriptMethod Open {if (-not $this.IsCpuEnabled) {throw 'CPU not enabled'}}
$script:fakeMonitor | Add-Member ScriptMethod Close {$script:closed=$true}
function New-PCBundledMonitor {return $script:fakeMonitor}
$result = Get-PCBundledTemperature
if ($null -eq $result -or $result.value -ne '55.0C') {throw 'CPU value missing'}
if (-not $script:closed) {throw 'monitor not closed'}
""")


    def test_missing_driver_and_unelevated_user_have_distinct_diagnostics(self):
        self.run_script(r"""
function Import-PCSensorLibrary {throw 'must not load hardware with missing prerequisites'}
function Get-PCSensorPrerequisites {return @{elevated=$false; version=$null}}
$result = Get-PCBundledTemperature
if ($null -ne $result -or ($script:PCDiagnostics['CPU温度'] -join ';') -notmatch 'CPU_TEMP_NOT_ELEVATED') {throw 'missing elevation diagnosis'}
$script:PCDiagnostics = [ordered]@{}
function Get-PCSensorPrerequisites {return @{elevated=$true; version=$null}}
$result = Get-PCBundledTemperature
if ($null -ne $result -or ($script:PCDiagnostics['CPU温度'] -join ';') -notmatch 'CPU_TEMP_PAWNIO_MISSING') {throw 'missing driver diagnosis'}
""")

    def test_wmi_ignores_gpu_and_invalid_values_without_loading_driver(self):
        self.run_script(r"""
function Get-PCBundledTemperature {throw 'must use available WMI CPU sensor'}
function Get-CimInstance {param($Namespace,$ClassName,$Filter,$ErrorAction)
 [pscustomobject]@{Parent='/gpu/0';Value=99}
 [pscustomobject]@{Parent='/intelcpu/0';Value=0}
 [pscustomobject]@{Parent='/intelcpu/0';Value=46}
 [pscustomobject]@{Parent='/intelcpu/0';Value=48}
}
$result = Get-PCCpuTemperature
if ($result.value -ne '48.0C' -or $result.source -ne 'root\LibreHardwareMonitor') {throw 'wrong CPU sensor'}
""")


    def test_no_sensors_keeps_null_and_closes_the_monitor(self):
        self.run_script(r"""
$script:closed=$false
$script:fakeMonitor=[pscustomobject]@{IsCpuEnabled=$false;Hardware=@()}
$script:fakeMonitor|Add-Member ScriptMethod Open {}
$script:fakeMonitor|Add-Member ScriptMethod Close {$script:closed=$true}
function New-PCBundledMonitor {return $script:fakeMonitor}
function Start-Sleep {param($Milliseconds)}
$result=Get-PCBundledTemperature
if ($null -ne $result -or -not $script:closed -or ($script:PCDiagnostics['CPU温度'] -join ';') -notmatch 'CPU_TEMP_NO_SENSOR') {throw 'missing evidence was fabricated or monitor left open'}
""")
