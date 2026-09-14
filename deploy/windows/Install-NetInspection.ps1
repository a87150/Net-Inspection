#Requires -Version 5.1
<#
.SYNOPSIS
Deploy this checkout on Windows Server. Run as administrator.
.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\Install-NetInspection.ps1
.EXAMPLE
.\deploy\windows\Install-NetInspection.ps1 -PythonExe C:\Python312\python.exe -ServiceCredential (Get-Credential)
#>
[CmdletBinding()]
param(
    [string]$PythonExe,
    [string]$EnvFile,
    [PSCredential]$ServiceCredential,
    [switch]$NonInteractive,
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $EnvFile) { $EnvFile = Join-Path $ProjectRoot '.env' }
$EnvFile = [IO.Path]::GetFullPath($EnvFile)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this script from an elevated Windows PowerShell.' }
Set-Location -LiteralPath $ProjectRoot
# Named mutex is released when this PowerShell process exits.
$installMutex = New-Object Threading.Mutex($false, 'Global\NetInspectionDeploy')
try { $hasInstallLock = $installMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $hasInstallLock = $true }
if (-not $hasInstallLock) { throw 'Another installation is running.' }

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Program (exit $LASTEXITCODE). See the preceding error." }
}
function Quote-Argument([string]$Value) {
    if ($Value.Contains('"') -or $Value.Contains("`r") -or $Value.Contains("`n")) { throw 'Paths cannot contain quotes or newlines.' }
    return '"' + $Value + '"'
}
function Protect-Config([string]$Path) {
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $account = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($account, 'FullControl', 'Allow')
        $acl.AddAccessRule($rule)
    }
    if ($ServiceCredential) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($ServiceCredential.UserName, 'Read', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

try {
if (-not $PythonExe) {
    if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
        $PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    } elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $PythonExe = (& py.exe -3 -c 'import sys; print(sys.executable)' | Select-Object -Last 1)
    } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        $PythonExe = (Get-Command python.exe).Source
    } else {
        throw 'Install 64-bit Python 3.12+ for all users first, then rerun. https://www.python.org/downloads/windows/'
    }
}
Invoke-Checked $PythonExe @('-c', 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ is required"; assert sys.maxsize > 2**32, "64-bit Python is required"')
$owner = "Network Inspection managed deployment: $ProjectRoot"
$taskNames = @('NetInspectionWeb', 'NetInspectionWorker')
foreach ($name in $taskNames) {
    if (Get-Service -Name $name -ErrorAction SilentlyContinue) {
        throw "Existing Windows service $name found. Keep one deployment method; stop and remove the old wrapper yourself before using scheduled tasks."
    }
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task -and $task.Description -ne $owner) { throw "Task $name is owned by another deployment; refusing to replace it." }
    if ($task -and -not $ServiceCredential -and $task.Principal.UserId -notin @('SYSTEM', 'S-1-5-18', 'NT AUTHORITY\SYSTEM')) {
        throw "Task $name uses a dedicated account. Rerun with -ServiceCredential (Get-Credential); refusing to silently switch to SYSTEM."
    }
}
# Stop only tasks installed by this script. Interrupted work recovers through DB leases.
foreach ($name in @('NetInspectionWorker', 'NetInspectionWeb')) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Disable-ScheduledTask -TaskName $name | Out-Null; Stop-ScheduledTask -TaskName $name }
}
$deadline = (Get-Date).AddSeconds(30)
do {
    $running = @($taskNames | ForEach-Object { Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue } | Where-Object State -eq 'Running')
    if (-not $running.Count) { break }
    if ((Get-Date) -ge $deadline) { throw 'Managed tasks did not stop. Resolve this before upgrading dependencies.' }
    Start-Sleep -Seconds 1
} while ($true)
$existing = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -match 'run_task_worker|deploy\.demo|deploy\.service|waitress|runserver' -and
    (($_.ExecutablePath -and $_.ExecutablePath.StartsWith((Join-Path $ProjectRoot '.venv'), [StringComparison]::OrdinalIgnoreCase)) -or $_.CommandLine.IndexOf($ProjectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)
})
if ($existing.Count) { throw ('Existing project processes: ' + (($existing | Select-Object -ExpandProperty ProcessId) -join ', ') + '. Stop their original launcher first.') }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { Invoke-Checked $PythonExe @('-m', 'venv', '.venv') }
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
Invoke-Checked $VenvPython @('-m', 'pip', 'install', '-r', 'requirements.lock.txt')
$commonArgs = @('--env-file', $EnvFile)
if ($NonInteractive) { $commonArgs += '--non-interactive' }
Invoke-Checked $VenvPython (@('-m', 'deploy.setup', 'configure') + $commonArgs)
Protect-Config $EnvFile
$RuntimePath = Join-Path $ProjectRoot 'runtime\deployment'
New-Item -ItemType Directory -Force -Path (Join-Path $RuntimePath 'logs') | Out-Null
if ($ServiceCredential) {
    Invoke-Checked 'icacls.exe' @($RuntimePath, '/grant', ($ServiceCredential.UserName + ':(OI)(CI)M'))
}
Invoke-Checked $VenvPython (@('-m', 'deploy.setup', 'preflight') + $commonArgs)
Invoke-Checked $VenvPython (@('-m', 'deploy.setup', 'prepare') + $commonArgs)
if ($PrepareOnly) { Write-Host 'Prepared. No scheduled tasks started.'; return }
$settings = New-ScheduledTaskSettingsSet -Disable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
foreach ($role in @('web', 'worker')) {
    $name = if ($role -eq 'web') { 'NetInspectionWeb' } else { 'NetInspectionWorker' }
    $arguments = '-u -m deploy.service ' + $role + ' --env-file ' + (Quote-Argument $EnvFile)
    $action = New-ScheduledTaskAction -Execute $VenvPython -Argument $arguments -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -AtStartup
    if ($ServiceCredential) {
        $taskPrincipal = New-ScheduledTaskPrincipal -UserId $ServiceCredential.UserName -LogonType Password -RunLevel Limited
    } else {
        $taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    }
    $definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal -Description $owner
    if ($ServiceCredential) {
        Register-ScheduledTask -TaskName $name -InputObject $definition -User $ServiceCredential.UserName -Password $ServiceCredential.GetNetworkCredential().Password -Force | Out-Null
    } else { Register-ScheduledTask -TaskName $name -InputObject $definition -Force | Out-Null }
}
try {
    Enable-ScheduledTask -TaskName NetInspectionWeb | Out-Null
    Start-ScheduledTask -TaskName NetInspectionWeb
    Invoke-Checked $VenvPython (@('-m', 'deploy.setup', 'probe') + $commonArgs)
    Enable-ScheduledTask -TaskName NetInspectionWorker | Out-Null
    Start-ScheduledTask -TaskName NetInspectionWorker
    Start-Sleep -Seconds 3
    foreach ($name in $taskNames) {
        if ((Get-ScheduledTask -TaskName $name).State -ne 'Running') { throw "$name failed to stay running. Inspect $RuntimePath\logs." }
    }
} catch {
    foreach ($name in $taskNames) { Disable-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Out-Null; Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue }
    throw
}
Write-Host 'Deployment complete. Web and Worker start automatically at boot.'
Write-Host "Configuration: $EnvFile"
Write-Host "Logs: $RuntimePath\logs"
Write-Host 'Allow the configured web port through your firewall only for the intended network; no firewall rules were changed.'

} finally {
    if ($hasInstallLock) { $installMutex.ReleaseMutex() }
    $installMutex.Dispose()
}
