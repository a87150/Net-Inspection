$ErrorActionPreference = 'Stop'
$agentPath = Join-Path $PSScriptRoot '../windows_agent/InspectionHttpService.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($agentPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Agent script does not parse' }
$functionAst = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-InspectionPayload' }, $true)
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
Write-Output 'Windows agent CPU-only and empty-selection tests passed.'
