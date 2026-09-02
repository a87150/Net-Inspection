# --- 配置部分 ---
# 请修改为您实际存放 OpenHardwareMonitorLib.dll 的路径
$dllPath = Join-Path $PSScriptRoot "OpenHardwareMonitorLib.dll"
$logTime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
# ---------------

# 1. 获取当前计算机名
$computerName = $env:COMPUTERNAME

# 2. 获取当前登录用户（注意：在管理员权限下可能显示的是运行命令的用户，而非交互式登录用户）
$loggedInUser = (Get-WmiObject -Class Win32_ComputerSystem).UserName

# 尝试从 LogonUI 注册表项获取最后登录用户的显示名称（更可靠）
$logonUIPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI"
try {
    $lastLoggedOnDisplayName = (Get-ItemProperty -Path $logonUIPath -Name "LastLoggedOnDisplayName" -ErrorAction Stop).LastLoggedOnDisplayName
} catch {
    $lastLoggedOnDisplayName = "未知"
}

# 3. 获取系统安装日期
$installDate = (Get-CimInstance Win32_OperatingSystem).InstallDate
$installDateLocal = $installDate.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss")

# 3.5 获取系统版本详细信息
$cvPath = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
$displayVersion = (Get-ItemProperty -Path $cvPath -Name "DisplayVersion" -ErrorAction SilentlyContinue).DisplayVersion
$editionID = (Get-ItemProperty -Path $cvPath -Name "EditionID" -ErrorAction SilentlyContinue).EditionID
$lcuVer = (Get-ItemProperty -Path $cvPath -Name "LCUVer" -ErrorAction SilentlyContinue).LCUVer

# 4. 获取系统上次启动时间
$bootTime = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$bootTimeLocal = $bootTime.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss")

# 5. 获取所有活动网络适配器的 IP 和 MAC 地址
$networkInfo = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object {
    $adapter = $_
    # 仅获取 IPv4 地址
    $ipAddrs = (Get-NetIPAddress -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4).IPAddress
    [PSCustomObject]@{
        接口名称 = $adapter.Name
        MAC地址 = $adapter.MacAddress
        IP地址  = ($ipAddrs -join ", ")
    }
}

# 5.5 获取计算机硬件资源情况
# --- 新增硬件资源获取代码块 ---
# 初始化硬件信息变量，用于在获取失败时显示
$cpuTempValue = "获取失败"
$cpuUsageValue = "获取失败"
$currentClockSpeedMHzValue = "获取失败"
$totalMemoryGBValue = "获取失败"
$memoryUsagePercentValue = "获取失败"
# --- 移除了所有与网卡速率相关的变量 ---

# --- 获取 CPU 温度 (使用 OpenHardwareMonitorLib) ---
# 检查 DLL 文件是否存在
if (Test-Path $dllPath) {
    try {
        # 加载 DLL
        Add-Type -Path $dllPath -ErrorAction Stop
        # 创建 OpenHardwareMonitor 计算机对象
        $computer = New-Object OpenHardwareMonitor.Hardware.Computer
        $computer.CPUEnabled = $true
        # 打开硬件监控
        $computer.Open()
        # 遍历硬件，查找 CPU
        foreach ($hardware in $computer.Hardware) {
            if ($hardware.HardwareType -eq [OpenHardwareMonitor.Hardware.HardwareType]::CPU) {
                $hardware.Update() # 更新硬件传感器数据
                # 遍历传感器，查找 CPU Package 温度
                foreach ($sensor in $hardware.Sensors) {
                    if ($sensor.SensorType -eq [OpenHardwareMonitor.Hardware.SensorType]::Temperature -and $sensor.Name -eq "CPU Package") {
                        $cpuTempValue = "$([math]::Round($sensor.Value, 2))°C"
                        break
                    }
                }
                break # 找到 CPU 后跳出循环
            }
        }
        $computer.Close() # 关闭监控
    } catch {
        $cpuTempValue = "DLL调用失败: $($_.Exception.Message)"
        Write-Warning "获取CPU温度失败: $_"
    }
} else {
    $cpuTempValue = "DLL文件未找到"
    Write-Warning "OpenHardwareMonitorLib.dll 未在指定路径找到: $dllPath"
}
# --- CPU 温度获取结束 ---

# --- CPU 使用率 (平均) ---
try {
    # 定义性能计数器路径
    $cpuCounter = "\Processor(_Total)\% Processor Time"
    # 采样两次，计算平均值
    $cpuSample = (Get-Counter -Counter $cpuCounter -SampleInterval 1 -MaxSamples 2).CounterSamples.CookedValue | Measure-Object -Average
    $cpuUsageValue = "$([math]::Round($cpuSample.Average, 2))%"
} catch {
    $cpuUsageValue = "获取失败: $_"
}

# --- CPU 当前频率 (MHz) ---
# 注意：Win32_Processor 的 CurrentClockSpeed 反映的是基础频率或最大频率，而非实时动态频率
try {
    $cpu = Get-WmiObject -Class Win32_Processor -ErrorAction Stop | Select-Object -First 1
    $currentClockSpeedMHzValue = "$($cpu.CurrentClockSpeed)MHz"
} catch {
    $currentClockSpeedMHzValue = "获取失败: $_"
}
# --- CPU 频率获取结束 ---

# --- 内存信息 ---
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $totalVisibleMemoryKB = $os.TotalVisibleMemorySize # KB
    $freePhysicalMemoryKB = $os.FreePhysicalMemory     # KB
    # 转换为 GB 并四舍五入
    $totalMemoryGBValue = "$([math]::Round($totalVisibleMemoryKB / 1MB, 2))GB"
    if ($totalVisibleMemoryKB -gt 0) {
        # 计算内存使用率百分比
        $memoryUsagePercentValue = "$([math]::Round((($totalVisibleMemoryKB - $freePhysicalMemoryKB) / $totalVisibleMemoryKB) * 100, 2))%"
    } else {
        $memoryUsagePercentValue = "计算失败"
    }
} catch {
    $totalMemoryGBValue = "获取失败: $_"
    $memoryUsagePercentValue = "获取失败: $_"
}

# --- 将硬件信息构造成 PSCustomObject 对象，单位在值中 ---
# --- 移除了网卡速率相关的属性 ---
$hardwareInfoObject = [PSCustomObject]@{
    当前CPU温度      = $cpuTempValue
    当前CPU占用率    = $cpuUsageValue
    当前CPU频率      = $currentClockSpeedMHzValue
    当前内存容量     = $totalMemoryGBValue
    当前内存使用率   = $memoryUsagePercentValue
    # 网卡速率信息已被移除
}
# --- 新增硬件资源获取代码块结束 ---


# 6. 获取 Windows 授权信息（slmgr /dlv）
$slmgrOutput = cscript.exe //nologo "C:\Windows\System32\slmgr.vbs" /dlv 2>&1
# 使用 OrderedDictionary 保持输出顺序
$licenseInfoOrdered = [System.Collections.Specialized.OrderedDictionary]::new()
foreach ($line in $slmgrOutput) {
    $trimmedLine = $line.Trim()
    if (-not $trimmedLine) { continue }  # 跳过空行
    # 尝试匹配 "键: 值" 格式
    if ($trimmedLine -match "^(.*?):\s*(.*)$") {
        $key = $matches[1].Trim()
        $value = $matches[2].Trim()
    } else {
        # 如果没有冒号，则整行作为键，值为空
        $key = $trimmedLine
        $value = ""
    }
    $licenseInfoOrdered[$key] = $value
}
# 转换为 PSCustomObject，以便 ConvertTo-Json 能正确处理
$licenseInfo = [PSCustomObject]@{}
foreach ($key in $licenseInfoOrdered.Keys) {
    Add-Member -InputObject $licenseInfo -MemberType NoteProperty -Name $key -Value $licenseInfoOrdered[$key]
}

# 7. 检测 KMS 服务器连通情况（ping 10.14.1.111）
$kmsIp = "10.14.1.111"
# 发送两次 ping 请求，返回布尔值
$pingResult = Test-Connection -ComputerName $kmsIp -Count 2 -Quiet
if ($pingResult) {
    $kmsStatus = "正常通讯"
} else {
    $kmsStatus = "无法访问"
}

# 8. 获取已安装软件列表
# 包括系统级和用户级的注册表卸载项，以及额外目录扫描
$installedApps = @()

# 8.1 获取系统级（HKLM）注册表中列出的已安装软件
$registryPaths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
)
foreach ($path in $registryPaths) {
    if (Test-Path $path) {
        Get-ChildItem -Path $path | ForEach-Object {
            $app = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
            if ($app.DisplayName) {
                $installedApps += [PSCustomObject]@{
                    软件名     = $app.DisplayName
                    版本       = $app.DisplayVersion
                    安装日期   = if ($app.InstallDate) { $app.InstallDate.ToString() } else { "" }
                    来源       = "HKLM"
                }
            }
        }
    }
}

# 8.3 获取用户级（HKU）注册表中列出的已安装软件
# 注意：需要管理员权限才能访问其他用户的注册表配置单元
# 使用 try...catch 处理权限不足的情况
try {
    # 获取所有用户 SID，排除 .DEFAULT 和一些系统账户
    $sids = Get-ChildItem -Path Registry::HKEY_USERS -ErrorAction Stop | # 在这里捕获主 Get-ChildItem 的错误
            Where-Object {
                $_.Name -notmatch '\.DEFAULT$' -and
                $_.Name -match 'S-1-5-21-\d+-\d+-\d+-\d+$' # 匹配常规用户SID
            }
    foreach ($sidKey in $sids) {
        $sid = Split-Path $sidKey.Name -Leaf
        $userUninstallKey = "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Uninstall"
        if (Test-Path $userUninstallKey) {
            Get-ChildItem -Path $userUninstallKey -ErrorAction SilentlyContinue | ForEach-Object {
                try {
                    $app = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
                    if ($app.DisplayName) {
                        $installedApps += [PSCustomObject]@{
                            软件名     = $app.DisplayName
                            版本       = $app.DisplayVersion
                            安装日期   = ""
                            来源       = "HKU\$sid"
                        }
                    }
                } catch {
                    # 捕获单个用户注册表项访问失败，避免整个循环中断
                    Write-Host "⚠️ 无法读取 $($_.PSPath)：$_" -ForegroundColor Yellow
                }
            }
        }
    }
} catch [System.Security.SecurityException] {
    # 捕获主 Get-ChildItem HKEY_USERS 的权限错误
    Write-Host "⚠️ 权限不足，无法访问其他用户的注册表卸载项。请以管理员身份运行脚本以获取完整信息。" -ForegroundColor Yellow
    # 脚本继续执行，只是不包含其他用户的软件列表
} catch {
    Write-Host "⚠️ 获取用户级软件列表时发生未知错误: $_" -ForegroundColor Red
}

# 8.4 扫描额外程序目录以发现未在注册表中列出的软件
$extraPaths = @(
    "${env:ProgramFiles}",
    "${env:ProgramFiles(x86)}",
    "C:\Software"
)
foreach ($dir in $extraPaths) {
    if (Test-Path $dir) {
        # 遍历目录下的子文件夹（通常代表一个软件）
        Get-ChildItem -Path $dir -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $folderName = $_.Name
            # 检查是否已存在于注册表列表中，避免重复
            if (-not ($installedApps.软件名 -contains $folderName)) {
                $installedApps += [PSCustomObject]@{
                    软件名     = $folderName
                    版本       = "未知 (来自目录扫描)"
                    安装日期   = ""
                    来源       = "目录扫描 ($dir)"
                }
            }
        }
    }
}

# 9. 解析 BitLocker 状态
$bitlockerRaw = (manage-bde -status)
$volumes = @()
$currentVol = $null
# 解析 manage-bde 输出
foreach ($line in $bitlockerRaw) {
    # 匹配卷信息行
    if ($line -match '^卷\s+(\w):\s+\[(.+)\]$') {
        if ($currentVol) { $volumes += $currentVol }
        $currentVol = [ordered]@{
            卷 = $matches[1]
            名称 = $matches[2]
        }
        continue
    }
    # 匹配卷类型行
    if ($line -match '^\[(.+)\]$' -and $currentVol) {
        $currentVol.类型 = $matches[1]
        continue
    }
    # 匹配键值对行
    if ($line -match '^\s+(.+?)\s*:\s*(.+)$' -and $currentVol) {
        $key = $matches[1].Trim()
        $val = $matches[2].Trim()
        # 映射键名
        switch ($key) {
            "大小"               { $field = "大小" }
            "BitLocker 版本"     { $field = "BitLocker版本" }
            "转换状态"           { $field = "转换状态" }
            "已加密百分比"       { $field = "已加密百分比" }
            "加密方法"           { $field = "加密方法" }
            "保护状态"           { $field = "保护状态" }
            "锁定状态"           { $field = "锁定状态" }
            "标识字段"           { $field = "标识字段" }
            "密钥保护器"         { $field = "密钥保护器" }
            "自动解锁"           { $field = "自动解锁" }
            default { $field = $key }
        }
        $currentVol[$field] = $val
    }
}
# 添加最后一个卷
if ($currentVol) { $volumes += $currentVol }
$bitlockerInfo = [ordered]@{
    磁盘卷信息 = $volumes
}

# 10. 获取 Windows Defender 状态
$defenderStatus = Get-MpComputerStatus -ErrorAction SilentlyContinue
$signatureVersion = if ($defenderStatus) { $defenderStatus.AntivirusSignatureVersion } else { "" }
$signatureUpdated = if ($defenderStatus -and $defenderStatus.AntivirusSignatureLastUpdated) {
    $defenderStatus.AntivirusSignatureLastUpdated.ToString("yyyy-MM-dd HH:mm:ss")
} else { "" }
$now = Get-Date
# 判断最近的扫描类型和时间
if ($defenderStatus.FullScanAge -ne 4294967295) { # 4294967295 表示从未扫描
    $scanType = "全盘扫描"
    $scanTime = $defenderStatus.FullScanStartTime
} elseif ($defenderStatus.QuickScanAge -ne 4294967295) {
    $scanType = "快速扫描"
    $scanTime = $defenderStatus.QuickScanStartTime
} else {
    $scanType = "未进行过扫描"
    $scanTime = $null
}
# 格式化扫描信息
if ($scanTime) {
    $formattedScanTime = $scanTime.ToString("yyyy-MM-dd HH:mm:ss")
    $timeDiff = $now - $scanTime
    $scanInfo = @{
        类型 = $scanType
        时间 = $formattedScanTime
        距今 = "$($timeDiff.Days)天 $($timeDiff.Hours)小时 $($timeDiff.Minutes)分钟"
    }
} else {
    $scanInfo = @{
        类型 = $scanType
        时间 = ""
        距今 = ""
    }
}

# 11. 获取系统更新历史记录（最近5条）
$updateHistory = @()
try {
    $updateSession = New-Object -ComObject Microsoft.Update.Session
    $updateSearcher = $updateSession.CreateUpdateSearcher()
    $history = $updateSearcher.QueryHistory(0, 5)
    $timeZone = [System.TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
    $updateHistory = $history | ForEach-Object {
        $convertedDate = [System.TimeZoneInfo]::ConvertTime($_.Date, [System.TimeZoneInfo]::Utc, $timeZone)
        [PSCustomObject]@{
            日期     = $convertedDate.ToString("yyyy-MM-dd HH:mm:ss")
            补丁名称 = $_.Title
            KB版本号 = if ($_.Title -match 'KB\d+') { $matches[0] } else { "" }
        }
    }
} catch {
    Write-Warning "获取系统更新历史失败: $_"
}

# 12. 汇总所有信息
$logData = [PSCustomObject]@{
    日志时间 = $logTime
    系统信息概览 = @{
        计算机名         = $computerName
        当前登录用户工号 = $loggedInUser
        当前登录用户姓名 = $lastLoggedOnDisplayName
        系统安装日期     = $installDateLocal
        开机时间         = $bootTimeLocal
        系统主要版本名   = $displayVersion
        系统版本类型     = $editionID
        系统详细版本     = $lcuVer
    }
    网络信息           = $networkInfo
    计算机硬件资源情况 = $hardwareInfoObject # 使用 PSCustomObject 对象 (已移除网卡速率)
    Windows激活信息   = $licenseInfo
    KMS服务器连通情况 = $kmsStatus
    已安装软件列表     = $installedApps | Sort-Object 软件名
    BitLocker状态     = $bitlockerInfo
    WindowsDefender状态 = @{
        当前病毒库版本 = $signatureVersion
        上次更新时间   = $signatureUpdated
        扫描信息       = $scanInfo
    }
    系统更新历史 = $updateHistory
}

# 13. 导出为 JSON 文件
$exportDir = "C:\Software"
if (-not (Test-Path $exportDir)) {
    New-Item -Path $exportDir -ItemType Directory -Force
}
$jsonPath = Join-Path -Path $exportDir -ChildPath "$computerName.json"
# Depth 增加以适应嵌套结构
$jsonBody = $logData | ConvertTo-Json -Depth 6
$jsonBody | Out-File -FilePath $jsonPath -Encoding UTF8

# 上传到巡检系统；上传失败时仍保留本地 JSON 和共享目录副本。
$apiUrl = if ($env:NET_INSPECTION_API_URL) { $env:NET_INSPECTION_API_URL } else { "http://127.0.0.1:8000/api/computer_inspection/" }
try {
    $utf8Body = [System.Text.Encoding]::UTF8.GetBytes($jsonBody)
    $response = Invoke-RestMethod -Uri $apiUrl -Method Post -Body $utf8Body -ContentType "application/json; charset=utf-8" -TimeoutSec 60
    Write-Host "✅ 日志已接收，日志 ID: $($response.log_id)，后台分析任务 ID: $($response.task_id)。此回执不是分析结果。" -ForegroundColor Green
} catch {
    Write-Warning "巡检数据上传失败，JSON 文件仍已保存在 $jsonPath。错误: $_"
}

# 14. 复制到共享路径
# 获取本机 DNS 服务器地址
$dnsServers = Get-DnsClientServerAddress -AddressFamily IPv4 |
              Select-Object -ExpandProperty ServerAddresses
# 定义 DNS 到共享路径的映射
$dnsToPathMap = @{
    "10.2.1.240"  = "\\10.2.1.240\Share\log\GetInfo\$computerName.json"
    "10.14.1.11"  = "\\10.14.1.11\Software\logs\GetInfo\$computerName.json"
    "10.15.1.240" = "\\10.15.1.240\Share\logs\GetInfo\$computerName.json"
}
foreach ($dns in $dnsToPathMap.Keys) {
    if ($dnsServers -contains $dns) {
        $destPath = $dnsToPathMap[$dns]
        try {
            Copy-Item -Path $jsonPath -Destination $destPath -Force
            Write-Host "✅ JSON 信息成功复制到 $destPath" -ForegroundColor Green
        } catch {
            Write-Host "❌ 复制到 $destPath 失败: $_" -ForegroundColor Red
        }
    }
}
