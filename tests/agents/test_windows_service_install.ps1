$ErrorActionPreference = 'Stop'
$agentPath = Join-Path $PSScriptRoot '../../agents/server/windows/InspectionHttpService.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($agentPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Agent script does not parse' }
foreach ($name in @('Install-InspectionService','Get-InspectionServiceHostSource')) {
    $functionAst=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    Invoke-Expression $functionAst.Extent.Text
}
$root=Join-Path (Resolve-Path (Join-Path $PSScriptRoot '../..')) ('.task6-artifacts/install-test-'+[guid]::NewGuid().ToString('N'))
$script:events=@(); $script:existing=$null; $script:failStart=$false; $script:portBusy=$false
# Only OS integration boundaries are mocked. Installer staging, config and rollback use real files.
function Assert-InspectionAdministrator { }
function Get-Acl { param($LiteralPath) return 'fixture-acl' }
function Set-Acl { param($LiteralPath,$AclObject) $script:events += 'restore-acl' }
function Set-InspectionDirectoryAccess { param($Directory) $script:events += 'secure-directory' }
function Add-Type { param($TypeDefinition,$Language,$ReferencedAssemblies,$OutputAssembly,$OutputType) [IO.File]::WriteAllText($OutputAssembly,'fixture-host') }
function Get-CimInstance { param($ClassName,$Filter) return $script:existing }
function Get-ScheduledTask { param($TaskName,$ErrorAction) return $null }
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) if ($script:portBusy) { return @{OwningProcess=123} } }
function New-Service { param($Name,$DisplayName,$BinaryPathName,$StartupType,$Description) $script:events += 'create'; if ($BinaryPathName -match 'fixture-token') { throw 'Token in command line' } }
function Set-Service { param($Name,$StartupType) $script:events += 'set-auto' }
function Stop-Service { param($Name,$ErrorAction) $script:events += 'stop' }
function Start-Service { param($Name,$ErrorAction) $script:events += 'start'; if ($script:failStart) { $script:failStart=$false; throw 'simulated startup failure' } }
function Get-Service { param($Name) return (New-Object psobject | Add-Member -MemberType ScriptMethod -Name WaitForStatus -Value {param($status,$timeout)} -PassThru) }
function Invoke-InspectionServiceControl { param([string[]]$Arguments) $script:events += ($Arguments -join ' '); if ($Arguments[0] -eq 'failure' -and $Arguments.Count -ne 6) { throw 'Incorrect recovery arguments' } }
function Get-NetFirewallRule { param($Name,$ErrorAction) return $null }
function New-NetFirewallRule { param($Name,$DisplayName,$Direction,$Action,$Protocol,$LocalPort,$Profile) $script:events += 'firewall' }
function Remove-NetFirewallRule { param($Name,$ErrorAction) $script:events += 'remove-firewall' }
Install-InspectionService -Source $agentPath -Directory $root -ListenPort 9180 -AgentToken 'fixture-token'
$config=Get-Content -LiteralPath (Join-Path $root 'settings.json') -Raw | ConvertFrom-Json
if ($config.port -ne 9180 -or $config.token -ne 'fixture-token') { throw 'Configuration not persisted' }
if (@($script:events | Where-Object { $_ -eq 'create' }).Count -ne 1) { throw 'Incorrect service creation' }
$script:existing=[pscustomobject]@{PathName='"'+(Join-Path $root 'InspectionServiceHost.exe')+'"'; State='Running'; StartName='LocalSystem'; ServiceType='Own Process'; StartMode='Auto'}
$script:failStart=$true
try { Install-InspectionService -Source $agentPath -Directory $root -ListenPort 9191 -AgentToken 'new-token'; throw 'Expected startup failure' }
catch { if ($_.Exception.Message -notmatch 'simulated startup failure') { throw } }
$config=Get-Content -LiteralPath (Join-Path $root 'settings.json') -Raw | ConvertFrom-Json
if ($config.port -ne 9180 -or $config.token -ne 'fixture-token') { throw 'Failed update did not restore configuration' }
if (@($script:events | Where-Object { $_ -eq 'create' }).Count -ne 1) { throw 'Update created duplicate service' }
$script:existing=$null; $script:portBusy=$true
$before=$script:events.Count
try { Install-InspectionService -Source $agentPath -Directory $root -ListenPort 9180 -AgentToken 'fixture-token'; throw 'Expected occupied-port failure' }
catch { if ($_.Exception.Message -notmatch 'port is already in use') { throw } }
if (@($script:events[$before..($script:events.Count-1)] | Where-Object { $_ -in @('create','start','stop') }).Count) { throw 'Occupied port caused service mutation' }
Write-Output 'PASS installer simulation: protected staging, fixed command, no duplicate creation, update rollback, occupied-port refusal.'

$script:portBusy=$false; $script:failStart=$true
$freshRoot=$root+'-retry'
try { Install-InspectionService -Source $agentPath -Directory $freshRoot -ListenPort 9180 -AgentToken 'fixture-token'; throw 'Expected first-install failure' }
catch { if ($_.Exception.Message -notmatch 'simulated startup failure') { throw } }
if (Test-Path -LiteralPath (Join-Path $freshRoot 'settings.json')) { throw 'Failed first installation retained token settings' }
Install-InspectionService -Source $agentPath -Directory $freshRoot -ListenPort 9180 -AgentToken 'fixture-token'
if (-not (Test-Path -LiteralPath (Join-Path $freshRoot 'settings.json'))) { throw 'Retry after failed first install did not succeed' }
Write-Output 'PASS first-install failure cleanup and safe retry.'
