$ErrorActionPreference='Stop'
$sourceScript=Join-Path $PSScriptRoot '../../net/scripts/templates/Install-PCCollector.ps1'
$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($sourceScript),[ref]$t,[ref]$e)
if($e.Count){throw 'Installer parse failed'}
$f=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Install-PCCollector'},$true)
Invoke-Expression $f.Extent.Text
function Assert-PCCollectorAdministrator {}
function Set-Acl {param($LiteralPath,$AclObject)}
$script:task=$null;$script:registered=0;$script:started=0;$script:fail=$false
function Get-ScheduledTask { [CmdletBinding()]param($TaskName,$TaskPath); return $script:task }
function Export-ScheduledTask {param($TaskName,$TaskPath); return '<old />'}
function New-ScheduledTaskAction {param($Execute,$WorkingDirectory); return [pscustomobject]@{Execute=$Execute;WorkingDirectory=$WorkingDirectory;Arguments=''} }
function New-ScheduledTaskTrigger {param([switch]$Once,[switch]$AtLogOn,$At,$RepetitionInterval); if($AtLogOn){return 'logon'}; if($RepetitionInterval.TotalHours -ne 2){throw 'Wrong repetition'}; return 'timer'}
function New-ScheduledTaskPrincipal {param($UserId,$LogonType,$RunLevel); if($UserId -ne 'SYSTEM' -or $RunLevel -ne 'Highest'){throw 'Wrong principal'}; return [pscustomobject]@{UserId=$UserId} }
function New-ScheduledTaskSettingsSet {param([switch]$StartWhenAvailable,$MultipleInstances,$ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries); if($MultipleInstances -ne 'IgnoreNew' -or $ExecutionTimeLimit.TotalMinutes -ne 20){throw 'Unsafe concurrency'}; return 'settings'}
function Register-ScheduledTask {param($TaskName,$TaskPath,$Action,$Trigger,$Principal,$Settings,$Description,[switch]$Force,$Xml); if($script:fail){throw 'Simulated registration failure'}; $script:registered++; $script:task=[pscustomobject]@{Actions=@($Action);Principal=$Principal;Trigger=@($Trigger);State='Ready'} }
function Start-ScheduledTask {param($TaskName,$TaskPath);$script:started++}
function Unregister-ScheduledTask {param($TaskName,$TaskPath,$Confirm);$script:task=$null}
$root=Join-Path ([IO.Path]::GetTempPath()) ('pc-install-test-'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root)|Out-Null
$source=Join-Path $root 'PCCollector.exe'
$destination=Join-Path $root 'installed'
$target=Join-Path $destination 'PCCollector.exe'
try {
 [IO.File]::WriteAllText($source,'old-exe')
 Install-PCCollector $root $destination
 if($script:registered -ne 1 -or $script:started -ne 1 -or [IO.File]::ReadAllText($target) -ne 'old-exe'){throw 'First install failed'}
 if(@($script:task.Trigger).Count -ne 2 -or $script:task.Trigger -notcontains 'logon'){throw 'Interactive logon trigger missing'}
 [IO.File]::WriteAllText($source,'new-exe')
 Install-PCCollector $root $destination
 if($script:registered -ne 2 -or [IO.File]::ReadAllText($target) -ne 'new-exe'){throw 'Update failed'}
 [IO.File]::WriteAllText($source,'bad-exe')
 $script:fail=$true
 try {Install-PCCollector $root $destination;throw 'Failure expected'} catch {if($_.Exception.Message -ne 'Simulated registration failure'){throw}}
 if([IO.File]::ReadAllText($target) -ne 'new-exe'){throw 'Failed update did not restore executable'}
 $script:fail=$false;$script:task.State='Running'
 try {Install-PCCollector $root $destination;throw 'Failure expected'} catch {if($_.Exception.Message -notlike '*currently running*'){throw}}
 $script:task.State='Ready';$script:task.Actions[0].Execute='unrelated.exe'
 try {Install-PCCollector $root $destination;throw 'Failure expected'} catch {if($_.Exception.Message -notlike '*unrelated scheduled task*'){throw}}
 Write-Output 'PASS installer simulation: SYSTEM, two-hour interval, IgnoreNew, idempotent update, rollback, running/foreign task refusal.'
} finally {
 foreach($file in @($source,$target,(Join-Path $destination '.pc-collector'))){if(Test-Path -LiteralPath $file){Remove-Item -LiteralPath $file}}
 if(Test-Path -LiteralPath $destination){Remove-Item -LiteralPath $destination}
 Remove-Item -LiteralPath $root
}
