#Requires -Version 5.1
<#
.SYNOPSIS
Operate the deployed Web and Worker scheduled tasks (status/start/stop/restart/probe/logs).
.EXAMPLE
.\deploy\windows\Manage-NetInspection.ps1 status
.EXAMPLE
.\deploy\windows\Manage-NetInspection.ps1 restart
.EXAMPLE
.\deploy\windows\Manage-NetInspection.ps1 logs web 80
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'start', 'stop', 'restart', 'enable', 'disable', 'probe', 'logs')]
    [string]$Action = 'status',
    [Parameter(Position = 1)]
    [ValidateSet('web', 'worker', 'both')]
    [string]$Role = 'both',
    [Parameter(Position = 2)]
    [int]$Lines = 80,
    [string]$EnvFile,
    [string]$PythonExe
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
Set-Location -LiteralPath $ProjectRoot
$RuntimePath = Join-Path $ProjectRoot 'runtime\deployment'
if (-not $PythonExe) { $PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not $EnvFile) { $EnvFile = Join-Path $ProjectRoot '.env' }
$tasks = @{ web = 'NetInspectionWeb'; worker = 'NetInspectionWorker' }
if ($Role -eq 'both') { $selected = @('web', 'worker') } else { $selected = @($Role) }

function Get-TaskState($name) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) { return 'Missing' }
    $info = Get-ScheduledTaskInfo -TaskName $name
    if ($task.State -ne 'Running') { return [string]$task.State }
    if ($info.LastTaskResult -ne 0) { return "Running(last=$($info.LastTaskResult))" }
    return 'Running'
}

switch ($Action) {
    'status' {
        $found = $false
        foreach ($r in $selected) {
            $state = Get-TaskState $tasks[$r]
            if ($state -ne 'Missing') { $found = $true }
            '{0,-8} {1,-22} {2}' -f $r, $tasks[$r], $state
        }
        if (-not $found) {
            Write-Host 'No deployment found. Run deploy\windows\Install-NetInspection.ps1 first.' -ForegroundColor Yellow
            exit 1
        }
    }
    'start'  { foreach ($r in $selected) { Start-ScheduledTask -TaskName $tasks[$r]; "started $($tasks[$r])" } }
    'stop'   { foreach ($r in $selected) { Stop-ScheduledTask  -TaskName $tasks[$r] -ErrorAction SilentlyContinue; "stopped  $($tasks[$r])" } }
    'restart' {
        foreach ($r in $selected) { Stop-ScheduledTask -TaskName $tasks[$r] -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
        foreach ($r in $selected) { Start-ScheduledTask -TaskName $tasks[$r]; "restarted $($tasks[$r])" }
        Start-Sleep -Seconds 3
        foreach ($r in $selected) { '{0,-8} {1,-22} {2}' -f $r, $tasks[$r], (Get-TaskState $tasks[$r]) }
    }
    'enable'  { foreach ($r in $selected) { Enable-ScheduledTask  -TaskName $tasks[$r]; "enabled  $($tasks[$r])" } }
    'disable' { foreach ($r in $selected) { Disable-ScheduledTask -TaskName $tasks[$r]; "disabled $($tasks[$r])" } }
    'probe' {
        if (-not (Test-Path $PythonExe)) { throw "Python not found: $PythonExe" }
        & $PythonExe -m deploy.setup probe --env-file $EnvFile
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    'logs' {
        $which = if ($Role -eq 'both') { 'web' } else { $Role }
        $log = Join-Path $RuntimePath "logs\$which.log"
        if (-not (Test-Path $log)) { throw "No log yet: $log (start the service first)" }
        Get-Content -LiteralPath $log -Tail $Lines
    }
}
