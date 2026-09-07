# Run repeatedly with the same local account (for example SYSTEM).
# PC_CONFIG: __PC_CONFIG_BASE64__
$ErrorActionPreference = 'Stop'
$ProfileConfig = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PC_CONFIG_BASE64__')) | ConvertFrom-Json
$PC_LOG_DESTINATION = $ProfileConfig.destination

function Publish-PCDaily {
    param([string]$Destination, [string]$StateDirectory, [string]$ComputerName, [scriptblock]$Collect)
    $safeName = $ComputerName -replace '[^a-zA-Z0-9._-]', '_'
    $day = Get-Date -Format 'yyyyMMdd'
    [IO.Directory]::CreateDirectory($StateDirectory) | Out-Null
    $marker = Join-Path $StateDirectory "$safeName-$day.done"
    $lock = $null
    $partial = $null
    try {
        $lock = [IO.File]::Open((Join-Path $StateDirectory "$safeName.lock"), 'OpenOrCreate', 'ReadWrite', 'None')
        if (Test-Path -LiteralPath $marker) { return }
        if (-not (Test-Path -LiteralPath $Destination -PathType Container)) { throw 'Shared folder is unavailable.' }
        $final = Join-Path $Destination "$safeName-$day.json"
        if (-not (Test-Path -LiteralPath $final)) {
            $json = & $Collect | ConvertTo-Json -Depth 12
            $partial = Join-Path $Destination ("$safeName-$day." + [guid]::NewGuid().ToString('N') + '.uploading')
            [IO.File]::WriteAllText($partial, $json, [Text.UTF8Encoding]::new($false))
            [IO.File]::Move($partial, $final)
            $partial = $null
        }
        [IO.File]::WriteAllText($marker, $day)
    } finally {
        if ($partial -and (Test-Path -LiteralPath $partial)) { Remove-Item -LiteralPath $partial -Force }
        if ($lock) { $lock.Dispose() }
    }
}

function Read-Optional {
    param([scriptblock]$Read)
    try { & $Read } catch { return $null }
}
function Format-PCDate($Value) {
    if ($Value) { return ([datetime]$Value).ToString('yyyy-MM-dd HH:mm:ss') }
    return $null
}

function Get-PCUserPolicies {
    param([string]$LoggedInUser)
    if ([string]::IsNullOrWhiteSpace($LoggedInUser)) { return $null }
    $reportPath = $null
    try {
        # Query local resultant policy for the interactive user, not the scheduled task account.
        $reportPath = [IO.Path]::GetTempFileName()
        gpresult.exe /USER $LoggedInUser /SCOPE USER /X $reportPath /F | Out-Null
        if ($LASTEXITCODE -ne 0) { return $null }
        $report = [xml]::new()
        $report.XmlResolver = $null
        $report.Load($reportPath)
        $userResults = $report.SelectSingleNode("/*[local-name()='Rsop']/*[local-name()='UserResults']")
        if ($null -eq $userResults) { return $null }
        return ,@($userResults.SelectNodes("*[local-name()='GPO']/*[local-name()='Name']") |
            ForEach-Object { $_.InnerText })
    } catch {
        return $null
    } finally {
        if ($reportPath -and (Test-Path -LiteralPath $reportPath)) {
            Remove-Item -LiteralPath $reportPath -Force
        }
    }
}

function Get-PCPayload {
$computerSystem = Get-CimInstance Win32_ComputerSystem
$operatingSystem = Get-CimInstance Win32_OperatingSystem
$processors = @(Get-CimInstance Win32_Processor)
$logicalDisks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')
$adapters = @(Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' | ForEach-Object {
    [PSCustomObject]@{
        '接口名称' = $_.Description
        'IP地址' = ($_.IPAddress | Where-Object { $_ -match '^\d+\.' }) -join ', '
        'MAC地址' = $_.MACAddress
    }
})
$physicalCores = ($processors | Measure-Object -Property NumberOfCores -Sum).Sum
$logicalProcessors = ($processors | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
$cpuUsage = ($processors | Measure-Object -Property LoadPercentage -Average).Average

$memoryTotalBytes = [double]$computerSystem.TotalPhysicalMemory
$memoryAvailableBytes = [double]$operatingSystem.FreePhysicalMemory * 1KB
$memoryUsage = if ($memoryTotalBytes -gt 0) {
    [math]::Round((1 - ($memoryAvailableBytes / $memoryTotalBytes)) * 100, 1)
} else {
    0
}
$invariantCulture = [Globalization.CultureInfo]::InvariantCulture
$cpuUsageText = if ($null -eq $cpuUsage) { '未知' } else { [string]::Format($invariantCulture, '{0:0.0}%', [math]::Max(0, [math]::Min(100, $cpuUsage))) }
$memoryUsageText = [string]::Format($invariantCulture, '{0:0.0}%', [math]::Max(0, [math]::Min(100, $memoryUsage)))
$diskSummary = ($logicalDisks | ForEach-Object { '{0} {1:N2} GB ({2:N2} GB free)' -f $_.DeviceID, ($_.Size / 1GB), ($_.FreeSpace / 1GB) }) -join '; '
$totalDiskGb = [math]::Round((($logicalDisks | Measure-Object -Property Size -Sum).Sum / 1GB), 2)
$computerName = $env:COMPUTERNAME

$payload = [ordered]@{
    'platform' = 'windows'
    '日志时间' = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    '系统信息概览' = [ordered]@{
        '计算机名' = $computerName
        '当前登录用户工号' = $computerSystem.UserName
        '当前登录用户姓名' = $computerSystem.UserName
        '开机时间' = $operatingSystem.LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss')
        '系统主要版本名' = $operatingSystem.Caption
        '系统详细版本' = $operatingSystem.Version
        '系统版本类型' = $operatingSystem.OSArchitecture
        '系统安装日期' = $operatingSystem.InstallDate.ToString('yyyy-MM-dd HH:mm:ss')
        'BIOS序列号' = (Read-Optional { (Get-CimInstance Win32_BIOS).SerialNumber })
        '制造商' = $computerSystem.Manufacturer
        '型号' = $computerSystem.Model
    }
    '网络信息' = $adapters
    '计算机硬件资源情况' = [ordered]@{
        '当前CPU温度' = $null
        '当前CPU频率' = [string]::Format($invariantCulture, '{0}MHz', $processors[0].CurrentClockSpeed)
        'CPU型号' = ($processors | Select-Object -First 1 -ExpandProperty Name)
        'CPU物理核心数' = $physicalCores
        'CPU逻辑处理器数' = $logicalProcessors
        '当前内存容量' = ('{0:N2}GB' -f ($computerSystem.TotalPhysicalMemory / 1GB))
        '当前CPU占用率' = $cpuUsageText
        '当前内存使用率' = $memoryUsageText
        '磁盘总量' = ('{0:N2}GB' -f $totalDiskGb)
        '磁盘摘要' = $diskSummary
        'cpu_physical_core_count' = $physicalCores
        'cpu_logical_processor_count' = $logicalProcessors
        'disk_summary' = $diskSummary
    }
    '日志文件元数据' = @()
}

$payload['Windows激活信息'] = Read-Optional {
    $license = Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" | Select-Object -First 1
    if ($license) {
        @{ '许可证状态' = if ($license.LicenseStatus -eq 1) { '已授权' } else { "未授权 ($($license.LicenseStatus))" }
           '名称' = $license.Name; '描述' = $license.Description }
    }
}
$payload['KMS服务器连通情况'] = Read-Optional {
    if (-not @($ProfileConfig.kms_servers).Count) { '未采集' }
    else {
        $reachable = $false
        foreach ($target in $ProfileConfig.kms_servers) {
            if (Test-Connection -ComputerName $target -Count 1 -Quiet -ErrorAction SilentlyContinue) {
                $reachable = $true
                break
            }
        }
        if ($reachable) { '正常通讯' } else { '无法访问' }
    }
}
$payload['当前与域服务器通讯情况'] = Read-Optional {
    if (-not $computerSystem.PartOfDomain) { '未加入域' }
    elseif (Test-ComputerSecureChannel -ErrorAction Stop) { '正常通讯' }
    else { '无法访问' }
}
$payload['已安装软件列表'] = Read-Optional {
    $roots = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
               'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')
    $roots += @(Get-ChildItem Registry::HKEY_USERS -ErrorAction Stop |
        Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' } |
        ForEach-Object { "$($_.PSPath)\Software\Microsoft\Windows\CurrentVersion\Uninstall" })
    $apps = @(foreach ($root in $roots) {
        if (Test-Path -LiteralPath $root) {
            foreach ($key in Get-ChildItem -LiteralPath $root -ErrorAction Stop) {
                $app = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction Stop
                if ($app.DisplayName) {
                    @{ '软件名' = $app.DisplayName; '版本' = $app.DisplayVersion
                       '安装日期' = $app.InstallDate; '来源' = $root }
                }
            }
        }
    })
    foreach ($folder in @($env:ProgramFiles, [Environment]::GetFolderPath('ProgramFilesX86'))) {
        if ($folder -and (Test-Path -LiteralPath $folder)) {
            foreach ($item in Get-ChildItem -LiteralPath $folder -Directory -ErrorAction Stop) {
                if ($apps.'软件名' -notcontains $item.Name) {
                    $apps += @{ '软件名' = $item.Name; '版本' = '未知'; '来源' = '目录扫描'; '安装日期' = '' }
                }
            }
        }
    }
    return ,$apps
}
$payload['当前运行进程清单'] = Read-Optional {
    return ,@(Get-Process -ErrorAction Stop | ForEach-Object {
        @{ '进程名' = $_.ProcessName; '进程ID' = $_.Id }
    })
}
$payload['BitLocker状态'] = Read-Optional {
    $volumes = @(Get-BitLockerVolume -ErrorAction Stop | ForEach-Object {
        @{ '卷' = $_.MountPoint
           '转换状态' = switch ([string]$_.VolumeStatus) {
               'FullyEncrypted' { '完全加密' }; 'FullyDecrypted' { '完全解密' }
               default { [string]$_.VolumeStatus }
           }
           '已加密百分比' = "$($_.EncryptionPercentage)%"
           '保护状态' = [string]$_.ProtectionStatus; '加密方法' = [string]$_.EncryptionMethod }
    })
    @{ '磁盘卷信息' = $volumes }
}
$payload['WindowsDefender状态'] = Read-Optional {
    $d = Get-MpComputerStatus -ErrorAction Stop
    $scan = @($d.FullScanStartTime, $d.QuickScanStartTime) | Where-Object { $_ } | Sort-Object -Descending | Select-Object -First 1
    @{ '当前病毒库版本' = $d.AntivirusSignatureVersion
       '上次更新时间' = Format-PCDate $d.AntivirusSignatureLastUpdated
       '扫描信息' = @{ '时间' = Format-PCDate $scan; '类型' = if ($scan -eq $d.FullScanStartTime) { '全盘扫描' } else { '快速扫描' } } }
}
$payload['系统更新历史'] = Read-Optional {
    $searcher = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $count = [math]::Min(5, $searcher.GetTotalHistoryCount())
    $history = @()
    if ($count -gt 0) {
        $history = @($searcher.QueryHistory(0, $count) | ForEach-Object {
            @{ '日期' = Format-PCDate ($_.Date.ToLocalTime()); '补丁名称' = $_.Title
               'KB版本号' = if ($_.Title -match 'KB\d+') { $matches[0] } else { '' } }
        })
    }
    return ,$history
}
$payload['已应用策略'] = @{
    '计算机策略' = Read-Optional {
        $policies = @(Get-CimInstance -Namespace root\RSOP\Computer -ClassName RSOP_GPO -ErrorAction Stop)
        return ,@($policies | ForEach-Object { $_.name })
    }
    '用户策略' = Get-PCUserPolicies $computerSystem.UserName
}
$payload['浏览器插件情况'] = Read-Optional {
    $profiles = @(Get-CimInstance Win32_UserProfile -Filter 'Special=False' -ErrorAction Stop)
    $browsers = @{ Chrome = 'Google\Chrome'; Edge = 'Microsoft\Edge' }
    $extensions = @{}
    foreach ($browser in $browsers.Keys) {
        $ids = @(foreach ($user in $profiles) {
            $browserRoot = Join-Path $user.LocalPath ("AppData\Local\" + $browsers[$browser] + '\User Data')
            if (Test-Path -LiteralPath $browserRoot) {
                foreach ($dir in Get-ChildItem -LiteralPath $browserRoot -Directory -ErrorAction Stop) {
                    $extensionRoot = Join-Path $dir.FullName 'Extensions'
                    if (Test-Path -LiteralPath $extensionRoot) {
                        Get-ChildItem -LiteralPath $extensionRoot -Directory -ErrorAction Stop | Select-Object -ExpandProperty Name
                    }
                }
            }
        })
        $extensions[$browser] = @($ids | Sort-Object -Unique)
    }
    $extensions
}
$payload['计算机和用户匹配情况'] = if (-not $computerSystem.UserName) { '未知' }
    elseif (($computerSystem.UserName -replace '^.*\\', '') -eq $computerName) { '正常' }
    else { '计算机名与登录用户名不匹配' }
$payload['事件发现'] = Read-Optional {
    $events = @()
    foreach ($log in @('System', 'Application')) {
        $queryErrors = @()
        $rows = @(Get-WinEvent -FilterHashtable @{ LogName = $log; StartTime = (Get-Date).AddDays(-1); Level = @(1,2,3) } -MaxEvents 100 -ErrorAction SilentlyContinue -ErrorVariable queryErrors)
        if (@($queryErrors | Where-Object { $_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*' }).Count) { throw 'Event log unavailable' }
        $events += @($rows | ForEach-Object {
            @{ '级别' = switch ($_.Level) { 1 { 'critical' }; 2 { 'error' }; 3 { 'warning' } }
               '消息' = $_.Message; '事件ID' = $_.Id; '时间' = Format-PCDate $_.TimeCreated; '日志' = $log }
        })
    }
    return ,$events
}
return $payload

}

# Collection entry point
$stateDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'PCDailyCollector'
Publish-PCDaily $PC_LOG_DESTINATION $stateDirectory $env:COMPUTERNAME { Get-PCPayload }
