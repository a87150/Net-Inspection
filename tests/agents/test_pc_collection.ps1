$ErrorActionPreference = 'Stop'
$agent = Join-Path $PSScriptRoot '../../net/scripts/templates/GetInfo_Upload.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($agent),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'PC script does not parse' }
foreach ($name in @('Add-PCDiagnostic','Get-PCInstalledSoftware','Get-PCCpuTemperature','Read-Optional','Read-PCCpuTemperatures','Get-PCBundledTemperature')) {
    $function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    Invoke-Expression $function.Extent.Text
}
$script:PCDiagnostics=[ordered]@{}
function Test-Path { param($LiteralPath) return $true }
function Get-ChildItem {
    [CmdletBinding()]param([Parameter(Position=0)]$Path, $LiteralPath)
    if ($Path -eq 'Registry::HKEY_USERS') { throw 'User hive denied' }
    [pscustomobject]@{PSPath='good'}
    [pscustomobject]@{PSPath='denied'}
    [pscustomobject]@{PSPath='second'}
}
function Get-ItemProperty {
    [CmdletBinding()]param($LiteralPath)
    if ($LiteralPath -eq 'denied') { throw 'Single key denied' }
    if ($LiteralPath -eq 'good') { [pscustomobject]@{DisplayName='Example';DisplayVersion='1';Publisher='Fixture'} }
    else { [pscustomobject]@{DisplayName='Other';DisplayVersion='2';Publisher='Fixture'} }
}
$apps=Get-PCInstalledSoftware
if ($apps.Count -ne 2 -or $apps[0].软件名 -ne 'Example' -or -not $script:PCDiagnostics['已安装软件列表']) { throw 'Partial software evidence lost or not deduplicated' }
function Get-CimInstance {
    [CmdletBinding()]param($Namespace,$ClassName,$Filter)
    if ($script:noSensors) { throw 'Provider unavailable' }
    [pscustomobject]@{Parent='/nvidiagpu/0';Value=99}
    [pscustomobject]@{Parent='/intelcpu/0';Value=$null}
    [pscustomobject]@{Parent='/intelcpu/0';Value=0}
    [pscustomobject]@{Parent='/intelcpu/0';Value=250}
    [pscustomobject]@{Parent='/intelcpu/0';Value=58}
    [pscustomobject]@{Parent='/intelcpu/0';Value=61.5}
}
$temperature=Get-PCCpuTemperature
if ($temperature.value -ne '61.5C') { throw 'Wrong sensor/value selected' }
$script:noSensors=$true
# A bundled monitor stub exercises CPU selection and cleanup without loading a driver.
$script:closed=$false
$cpu=[pscustomobject]@{HardwareType='CPU'; Sensors=@([pscustomobject]@{SensorType='Temperature'; Value=63}); SubHardware=@()}
$cpu | Add-Member ScriptMethod Update { }
$gpu=[pscustomobject]@{HardwareType='GpuNvidia'; Sensors=@([pscustomobject]@{SensorType='Temperature';Value=99});SubHardware=@()}
$gpu | Add-Member ScriptMethod Update { throw 'GPU must not be sampled' }
$script:monitor=[pscustomobject]@{CPUEnabled=$false; Hardware=@($cpu,$gpu)}
$script:monitor | Add-Member ScriptMethod Open { if (-not $this.CPUEnabled) { throw 'CPU disabled' } }
$script:monitor | Add-Member ScriptMethod Close { $script:closed=$true }
function New-PCBundledMonitor { return $script:monitor }
$temperature=Get-PCCpuTemperature
if ($temperature.value -ne '63.0C' -or -not $script:closed) { throw 'Bundled fallback or close failed' }
$script:closed=$false
$cpu | Add-Member ScriptMethod Update { throw 'sensor read failed' } -Force
if ($null -ne (Get-PCCpuTemperature) -or -not $script:closed) { throw 'Failure did not release monitor' }
function New-PCBundledMonitor { throw 'Missing hardware library' }
if ($null -ne (Get-PCCpuTemperature) -or -not $script:PCDiagnostics['CPU温度']) { throw 'Unavailable temperature not explained' }
# Test the production process block without running the collector entry point.
$content=[IO.File]::ReadAllText($agent)
$processBlock=$content.Split(@("$"+"payload['当前运行进程清单'] = "),[StringSplitOptions]::None)[1].Split(@("$"+"payload['BitLocker状态']"),[StringSplitOptions]::None)[0]
function Get-Process { [CmdletBinding()]param(); @('chrome','chrome','svchost','CHROME') | ForEach-Object { [pscustomobject]@{ProcessName=$_} } }
$names=Invoke-Expression $processBlock
if ($names.Count -ne 2 -or @($names | Where-Object { $_ -isnot [string] }).Count) { throw 'Processes are not unique names' }
Write-Output 'PASS PC collection: partial registry results, duplicate apps/processes, CPU-only sensors and missing diagnostics.'
