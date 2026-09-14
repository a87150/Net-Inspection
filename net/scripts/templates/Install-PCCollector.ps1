param([string]$InstallDirectory = (Join-Path $env:ProgramData 'PCDailyCollector'))
$ErrorActionPreference = 'Stop'
function Assert-PCCollectorAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this installer as administrator or deploy it as a computer startup script (SYSTEM).' }
}
function Install-PCCollector {
    param([string]$SourceDirectory, [string]$Directory)
    Assert-PCCollectorAdministrator
    $directoryPath = [IO.Path]::GetFullPath($Directory)
    $cursor = $directoryPath
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Installation path cannot contain links.' }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    $source = Join-Path $SourceDirectory 'PCCollector.exe'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Extract the ZIP completely; PCCollector.exe is missing.' }
    $target = Join-Path $directoryPath 'PCCollector.exe'
    if ([IO.Path]::GetFullPath($source) -eq $target) { throw 'Run the installer from the extracted download directory, not the installed directory.' }
    $marker = Join-Path $directoryPath '.pc-collector'
    $taskName = 'PCDailyCollector'
    $mutex = New-Object Threading.Mutex($false, 'Global\PCDailyCollectorInstall')
    $locked=$false; $registered=$false; $oldExe=$null; $oldXml=$null
    try {
        try { $locked=$mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked=$true }
        if (-not $locked) { throw 'Another collector installation is in progress.' }
        if (Test-Path -LiteralPath $directoryPath) {
            if (-not (Test-Path -LiteralPath $marker) -and @(Get-ChildItem -LiteralPath $directoryPath -Force).Count) { throw 'Refusing to overwrite a directory not owned by this collector.' }
            foreach ($path in @($marker,$target)) {
                if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Installation files cannot be links.' }
            }
            if ((Test-Path -LiteralPath $marker) -and [IO.File]::ReadAllText($marker) -ne 'PCDailyCollector/v1') { throw 'Unknown collector installation marker.' }
        }
        $existing=Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
        if ($existing) {
            if (@($existing.Actions).Count -ne 1 -or $existing.Actions[0].Execute -ne $target -or $existing.Actions[0].Arguments -or $existing.Principal.UserId -notin @('SYSTEM','S-1-5-18','NT AUTHORITY\SYSTEM')) { throw 'An unrelated scheduled task uses this name; refusing to replace it.' }
            if ($existing.State -eq 'Running') { throw 'Collector is currently running; retry installation after it finishes.' }
            $oldXml=Export-ScheduledTask -TaskName $taskName -TaskPath '\'
        }
        if (Test-Path -LiteralPath $target) { $oldExe=[IO.File]::ReadAllBytes($target) }
        [IO.Directory]::CreateDirectory($directoryPath) | Out-Null
        $acl=New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true,$false)
        $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
        foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
            $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
        }
        Set-Acl -LiteralPath $directoryPath -AclObject $acl
        [IO.File]::WriteAllText($marker,'PCDailyCollector/v1')
        try {
            [IO.File]::WriteAllBytes($target,[IO.File]::ReadAllBytes($source))
            foreach ($file in @($target,$marker)) {
                $fileAcl=New-Object Security.AccessControl.FileSecurity
                $fileAcl.SetAccessRuleProtection($true,$false)
                $fileAcl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
                foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
                    $fileAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)), 'FullControl', 'Allow')))
                }
                Set-Acl -LiteralPath $file -AclObject $fileAcl
            }
            $action=New-ScheduledTaskAction -Execute $target -WorkingDirectory $directoryPath
            $trigger=@(New-ScheduledTaskTrigger -Once -At (Get-Date).AddHours(2) -RepetitionInterval (New-TimeSpan -Hours 2); New-ScheduledTaskTrigger -AtLogOn)
            $identity=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $identity -Settings $settings -Description 'PC daily log collector; retries every two hours, one successful upload per day.' -Force | Out-Null
            $registered=$true
            Start-ScheduledTask -TaskName $taskName -TaskPath '\'
        } catch {
            $failure=$_
            if ($registered) {
                if ($oldXml) { Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Xml $oldXml -Force | Out-Null }
                else { Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false }
            }
            if ($null -ne $oldExe) { [IO.File]::WriteAllBytes($target,$oldExe) }
            elseif (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target }
            throw $failure
        }
        Write-Host "Installed $target. Task $taskName runs as SYSTEM every two hours; first run requested."
        Write-Host 'Allow the client computer account to write to the configured network share. Task completion/result must be checked separately.'
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
Install-PCCollector -SourceDirectory $PSScriptRoot -Directory $InstallDirectory
