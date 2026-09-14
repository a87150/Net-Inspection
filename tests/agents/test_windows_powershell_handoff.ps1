$ErrorActionPreference = 'Stop'
$agentPath = Join-Path $PSScriptRoot '../../agents/server/windows/InspectionHttpService.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($agentPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Agent script parse failed' }
$helper=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-InspectionWindowsPowerShell'},$true)
if (-not $helper) { throw 'Missing Windows PowerShell handoff' }
Invoke-Expression $helper.Extent.Text
$root=Join-Path ([IO.Path]::GetTempPath()) ('inspection-handoff-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$path=Join-Path $root ('fixture space ' + [char]0x6D4B + '.ps1')
$output=Join-Path $root 'result.json'
$previousModulePath=$env:PSModulePath
$previousOutput=$env:INSPECTION_HANDOFF_TEST_OUTPUT
$env:INSPECTION_HANDOFF_TEST_OUTPUT=$output
$content=@'
param([int]$Port=9180,[string]$Token='default',[switch]$Install,[switch]$Console,[switch]$RunService)
if ($RunService) { exit 23 }
if ($Console) { throw ("fixture diagnostic " + [char]0x6D4B + " " + $Token) }
# Exercise real Desktop module loading, without changing ACLs or installing services.
$acl = Get-Acl -LiteralPath $PSCommandPath
if (-not $acl.Owner) { throw 'Cannot read fixture ACL' }
foreach ($name in @('Set-Acl', 'Get-CimInstance', 'Get-ScheduledTask', 'Get-NetTCPConnection', 'Get-NetFirewallRule', 'New-Service')) {
    $command = Get-Command $name -ErrorAction Stop
    Import-Module $command.ModuleName -ErrorAction Stop
}
@{port=$Port;token=$Token;install=[bool]$Install;console=[bool]$Console;edition=$PSVersionTable.PSEdition;bound=@($PSBoundParameters.Keys);command=[Environment]::CommandLine} | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:INSPECTION_HANDOFF_TEST_OUTPUT -Encoding UTF8
'@
try {
    [IO.File]::WriteAllText($path,$content)
    $token='fixture space " quote $value &; ' + [char]39 + [char]0x6D4B
    $code=Invoke-InspectionWindowsPowerShell -ScriptPath $path -Parameters @{Port=9222;Token=$token;Install=[Management.Automation.SwitchParameter]$true;Console=[Management.Automation.SwitchParameter]$false}
    if ($code -ne 0) { throw 'Handoff failed' }
    $data=Get-Content -LiteralPath $output -Raw | ConvertFrom-Json
    if ($data.edition -ne 'Desktop' -or $data.port -ne 9222 -or $data.token -cne $token -or -not $data.install -or $data.console) { throw 'Forwarded arguments changed' }
    if ($data.command.Contains('fixture space')) { throw 'Token in child command line' }
    $code=Invoke-InspectionWindowsPowerShell -ScriptPath $path -Parameters @{}
    $data=Get-Content -LiteralPath $output -Raw | ConvertFrom-Json
    if ($code -ne 0 -or $data.port -ne 9180 -or $data.token -ne 'default' -or $data.bound.Count -ne 0) { throw 'Omitted parameters were supplied, breaking updates' }
    $failure = ''
    try { Invoke-InspectionWindowsPowerShell -ScriptPath $path -Parameters @{RunService=$true} }
    catch { $failure = $_.Exception.Message }
    if ($failure -notmatch 'exit 23') { throw 'Child failure code lost' }
    $failure = ''
    try { Invoke-InspectionWindowsPowerShell -ScriptPath $path -Parameters @{Console=$true;Token=$token} }
    catch { $failure = $_.Exception.Message }
    if (-not $failure.Contains(('fixture diagnostic ' + [char]0x6D4B)) -or $failure -notmatch 'script line 3' -or $failure.Contains($token) -or $failure -notmatch 'REDACTED') { throw ('Child diagnostic missing or secret leaked: ' + $failure) }
    if ($env:PSModulePath -cne $previousModulePath) { throw 'Parent module path changed' }
    Write-Output 'PASS real Windows PowerShell handoff: real ACL and system modules, unchanged parent environment, special characters, switches, omitted arguments, token protection and failure exit.'
} finally {
    $env:INSPECTION_HANDOFF_TEST_OUTPUT=$previousOutput
    # Delete only the two test files and then the empty directory created above.
    foreach ($file in @($path,$output)) { if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file } }
    Remove-Item -LiteralPath $root
}
