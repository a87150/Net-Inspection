# This file is generated for one inspection profile. It contains no directory-service,
# device, application, or database credentials.
$ErrorActionPreference = 'Stop'

$ProfileConfig = @'
__PROFILE_CONFIG_JSON__
'@ | ConvertFrom-Json

function Get-RecentLogMetadata {
    param([object]$Config)
    $now = Get-Date
    $start = $null
    $endExclusive = $null
    if ($Config.file_time_mode -eq 'recent_days') {
        $start = $now.AddDays(-[int]$Config.recent_days)
    } elseif ($Config.file_time_mode -eq 'date_range') {
        $start = [datetime]::ParseExact($Config.range_start_date, 'yyyy-MM-dd', $null)
        $endExclusive = [datetime]::ParseExact($Config.range_end_date, 'yyyy-MM-dd', $null).AddDays(1)
    }

    $items = @()
    foreach ($directory in @($Config.scan_directories)) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            Write-Warning "Configured scan directory is unavailable: $directory"
            continue
        }
        $files = Get-ChildItem -LiteralPath $directory -Filter '*.json' -File -Force -ErrorAction SilentlyContinue
        if ($Config.recursive) {
            $files = Get-ChildItem -LiteralPath $directory -Filter '*.json' -File -Force -Recurse -ErrorAction SilentlyContinue
        }
        foreach ($file in $files) {
            $matchesWindow = if ($start -and $endExclusive) {
                $file.LastWriteTime -ge $start -and $file.LastWriteTime -lt $endExclusive
            } elseif ($start) {
                $file.LastWriteTime -ge $start -and $file.LastWriteTime -le $now
            } else {
                $false
            }
            if ($matchesWindow) {
                $items += [PSCustomObject]@{
                    path = $file.FullName
                    modified_at = $file.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
                    size_bytes = $file.Length
                }
            }
        }
    }
    return $items
}

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
if ($null -eq $cpuUsage) { $cpuUsage = 0 }
$memoryTotalBytes = [double]$computerSystem.TotalPhysicalMemory
$memoryAvailableBytes = [double]$operatingSystem.FreePhysicalMemory * 1KB
$memoryUsage = if ($memoryTotalBytes -gt 0) {
    [math]::Round((1 - ($memoryAvailableBytes / $memoryTotalBytes)) * 100, 1)
} else {
    0
}
$invariantCulture = [Globalization.CultureInfo]::InvariantCulture
$cpuUsageText = [string]::Format($invariantCulture, '{0:0.0}%', [math]::Max(0, [math]::Min(100, $cpuUsage)))
$memoryUsageText = [string]::Format($invariantCulture, '{0:0.0}%', [math]::Max(0, [math]::Min(100, $memoryUsage)))
$diskSummary = ($logicalDisks | ForEach-Object { '{0} {1:N2} GB ({2:N2} GB free)' -f $_.DeviceID, ($_.Size / 1GB), ($_.FreeSpace / 1GB) }) -join '; '
$totalDiskGb = [math]::Round((($logicalDisks | Measure-Object -Property Size -Sum).Sum / 1GB), 2)
$computerName = $env:COMPUTERNAME

$payload = [ordered]@{
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
        '制造商' = $computerSystem.Manufacturer
        '型号' = $computerSystem.Model
    }
    '网络信息' = $adapters
    '计算机硬件资源情况' = [ordered]@{
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
    '日志文件元数据' = @(Get-RecentLogMetadata $ProfileConfig)
}
$jsonBody = $payload | ConvertTo-Json -Depth 8

try {
    $utf8Body = [System.Text.Encoding]::UTF8.GetBytes($jsonBody)
    Invoke-RestMethod -Uri $ProfileConfig.upload_url -Method Post -Body $utf8Body -ContentType 'application/json; charset=utf-8' -TimeoutSec 60 | Out-Null
    Write-Host 'Inspection payload uploaded.'
} catch {
    $failureDirectory = [string]$ProfileConfig.scan_directories[0]
    New-Item -ItemType Directory -Path $failureDirectory -Force | Out-Null
    $failurePath = Join-Path $failureDirectory ("{0}-upload-failed-{1}.json" -f $computerName, (Get-Date -Format 'yyyyMMddHHmmss'))
    [System.IO.File]::WriteAllText($failurePath, $jsonBody, [System.Text.UTF8Encoding]::new($false))
    Write-Error "Inspection upload failed; payload retained at $failurePath. $_"
    exit 1
}
