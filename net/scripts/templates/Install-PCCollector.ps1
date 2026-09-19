param([string]$InstallDirectory = (Join-Path $env:ProgramData 'PCDailyCollector'), [switch]$SkipTemperatureDriver)
$ErrorActionPreference = 'Stop'
function Assert-PCCollectorAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this installer as administrator or deploy it as a computer startup script (SYSTEM).' }
}
function Get-PCPawnIOVersion {
    $registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine', 'Registry64')
    try {
        $key = $registry.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO')
        if ($key) { try { return [version]$key.GetValue('DisplayVersion') } finally { $key.Dispose() } }
    } finally { $registry.Dispose() }
    return $null
}
function Install-PCSensorDriver {
    param([string]$SourceDirectory, [string]$Directory)
    $version = Get-PCPawnIOVersion
    if ($null -ne $version -and $version -ge [version]'2.2.0') { return }
    $source = Join-Path $SourceDirectory 'PawnIO_setup.exe'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'PawnIO_setup.exe is missing. Extract the complete updated ZIP or use -SkipTemperatureDriver when managed separately.' }
    # Execute only a pinned official installer copied into the protected install directory.
    $installer = Join-Path $Directory ('.pawnio-' + [guid]::NewGuid().ToString('N') + '.exe')
    $process = $null
    try {
        [IO.File]::WriteAllBytes($installer, [IO.File]::ReadAllBytes($source))
        if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne '1F519A22E47187F70A1379A48CA604981C4FCF694F4E65B734AAA74A9FBA3032') { throw 'PawnIO installer checksum mismatch.' }
        if ((Get-AuthenticodeSignature -LiteralPath $installer).Status -ne 'Valid') { throw 'PawnIO installer signature is not trusted on this computer.' }
        $process = Start-Process -FilePath $installer -ArgumentList '-install','-silent' -WindowStyle Hidden -PassThru
        if (-not $process.WaitForExit(120000)) { throw 'PawnIO installation did not finish within two minutes. Check the installer before retrying; it was not interrupted.' }
        if ($process.ExitCode -ne 0) { throw ('PawnIO installation failed (exit {0}).' -f $process.ExitCode) }
        $version = Get-PCPawnIOVersion
        if ($null -eq $version -or $version -lt [version]'2.2.0') { throw 'PawnIO installation did not register version 2.2.0 or later.' }
    } finally {
        if ($null -eq $process -or $process.HasExited) {
            if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer -Force }
        }
        if ($process) { $process.Dispose() }
    }
}
function Install-PCCollector {
    param([string]$SourceDirectory, [string]$Directory, [switch]$SkipTemperatureDriver)
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
            if (-not $SkipTemperatureDriver) { Install-PCSensorDriver -SourceDirectory $SourceDirectory -Directory $directoryPath }
            else { Write-Warning 'Temperature driver installation skipped; other collection continues without guaranteed CPU temperature.' }
            $action=New-ScheduledTaskAction -Execute $target -WorkingDirectory $directoryPath
            $trigger=@(New-ScheduledTaskTrigger -Once -At (Get-Date).AddHours(2) -RepetitionInterval (New-TimeSpan -Hours 2); New-ScheduledTaskTrigger -AtLogOn)
            $identity=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $identity -Settings $settings -Description 'PC collector; saves latest JSON locally then posts it to the monitoring API every two hours.' -Force | Out-Null
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
        Write-Host "Installed $target. Task $taskName runs as SYSTEM every two hours and at user login; first run requested."
        Write-Host 'Each run replaces LocalApplicationData\PCDailyCollector\latest.json, then posts the same UTF-8 JSON to the configured monitoring API. Check task history and collector.log for API failures.'
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
Install-PCCollector -SourceDirectory $PSScriptRoot -Directory $InstallDirectory -SkipTemperatureDriver:$SkipTemperatureDriver
