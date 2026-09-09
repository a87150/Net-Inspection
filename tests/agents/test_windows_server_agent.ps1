$ErrorActionPreference = 'Stop'
$agentPath = Join-Path $PSScriptRoot '../../agents/server/windows/InspectionHttpService.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($agentPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Agent script does not parse' }
$helperAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Add-CollectionError' }, $true)
$functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-InspectionPayload' }, $true)
Invoke-Expression $helperAst.Extent.Text
Invoke-Expression $functionAst.Extent.Text
$script:queried = @()
function Get-CimInstance {
    param($ClassName)
    $script:queried += $ClassName
    if ($ClassName -ne 'Win32_Processor') { throw "Unselected CIM query: $ClassName" }
    [pscustomobject]@{ LoadPercentage = 12; NumberOfLogicalProcessors = 4 }
}
function Get-Service { throw 'Unselected services query' }
function Get-WinEvent { throw 'Unselected logs query' }
function Get-NetIPConfiguration { throw 'Unselected network query' }
$payload = Get-InspectionPayload -Fields @('cpu')
if (($payload.Keys | Sort-Object) -join ',' -ne 'collected_at,cpu') { throw 'Unselected fields returned' }
if ($payload.cpu.usage_percent -ne 12) { throw 'CPU data missing' }
if ($script:queried.Count -ne 1) { throw 'Unexpected query count' }
$payload = Get-InspectionPayload -Fields @()
if (($payload.Keys | Sort-Object) -join ',' -ne 'collected_at') { throw 'Empty selection collects data' }
# A service can enumerate successfully but reject its StartType query under the
# SYSTEM account. That must not turn otherwise useful inspection evidence into HTTP 500.
function Get-CimInstance {
    param($ClassName)
    if ($ClassName -ne 'Win32_Processor') { throw "Unexpected CIM query: $ClassName" }
    [pscustomobject]@{ LoadPercentage = 12; NumberOfLogicalProcessors = 4 }
}
function Get-Service { throw "Service 'Denied' cannot be queried: PermissionDenied" }
$payload = Get-InspectionPayload -Fields @('cpu', 'services')
if ($payload.cpu.usage_percent -ne 12) { throw 'A service property error discarded CPU evidence' }
if (-not $payload.collection_errors['services'] -or $payload.collection_errors['services'][0] -notmatch 'PermissionDenied') { throw 'Unreadable service error was not reported' }
Write-Output 'Windows agent CPU-only and empty-selection tests passed.'

# Nonterminating service errors still preserve readable service evidence.
function Get-Service {
    [CmdletBinding()]param()
    Write-Error 'PermissionDenied during enumeration'
    [pscustomobject]@{Name='StoppedAuto'; DisplayName='Stopped automatic service'; Status='Stopped'; StartType='Automatic'}
}
$payload = Get-InspectionPayload -Fields @('services')
if ($payload.services.Count -ne 1 -or -not $payload.collection_errors.services) { throw 'Partial service enumeration not preserved' }
function Get-WinEvent {
    [CmdletBinding()]param($FilterHashtable, $MaxEvents)
    Write-Error -Message 'No matching events' -ErrorId 'NoMatchingEventsFound'
}
$payload = Get-InspectionPayload -Fields @('logs')
if ($payload.logs.Count -ne 0 -or $payload.Contains('collection_errors')) { throw 'Empty event log incorrectly failed' }
function Get-CimInstance { param($ClassName) throw 'PermissionDenied on CIM' }
$payload = Get-InspectionPayload -Fields @('cpu', 'memory', 'system_info')
foreach ($field in @('cpu', 'memory', 'system_info')) {
    if (-not $payload.collection_errors[$field][0]) { throw "Missing CIM error: $field" }
}
$Token = 'fixture-secret'
$errors = [ordered]@{}
Add-CollectionError $errors 'cpu' 'failure fixture-secret'
if ($errors.cpu[0] -match 'fixture-secret') { throw 'Token disclosed in diagnostic' }
Write-Output 'Windows agent field isolation, empty logs and token protection passed.'
