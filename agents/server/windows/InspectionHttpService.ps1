param(
    [int]$Port = 9180,
    [string]$Token = $env:NET_INSPECTION_AGENT_TOKEN
)

$ErrorActionPreference = 'Stop'
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://+:$Port/")
$listener.Start()
Write-Host "Windows inspection HTTP service listening on port $Port"

function Get-InspectionPayload {
    param([string[]]$Fields = @('computer_name', 'system_info', 'cpu', 'memory', 'storage_status', 'network_info', 'services', 'logs'))
    $payload = [ordered]@{ collected_at = (Get-Date).ToString('o') }
    if ($Fields -contains 'computer_name') { $payload.computer_name = $env:COMPUTERNAME }
    if (($Fields -contains 'system_info') -or ($Fields -contains 'memory')) {
        $os = Get-CimInstance Win32_OperatingSystem
    }
    if ($Fields -contains 'system_info') {
        $payload.system_info = [ordered]@{
            caption = $os.Caption
            version = $os.Version
            build_number = $os.BuildNumber
            last_boot_time = $os.LastBootUpTime
            uptime_seconds = [int64]((Get-Date) - $os.LastBootUpTime).TotalSeconds
        }
    }
    if ($Fields -contains 'cpu') {
        $processors = @(Get-CimInstance Win32_Processor)
        $cpuLoad = ($processors | Measure-Object -Property LoadPercentage -Average).Average
        $payload.cpu = [ordered]@{usage_percent=[math]::Round([double]$cpuLoad, 2); logical_processors=($processors.NumberOfLogicalProcessors | Measure-Object -Sum).Sum}
    }
    if ($Fields -contains 'memory') {
        $payload.memory = [ordered]@{
            total_bytes = [int64]$os.TotalVisibleMemorySize * 1024
            free_bytes = [int64]$os.FreePhysicalMemory * 1024
            used_percent = [math]::Round((1 - ($os.FreePhysicalMemory / $os.TotalVisibleMemorySize)) * 100, 2)
        }
    }
    if ($Fields -contains 'storage_status') {
        $payload.storage_status = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
        [ordered]@{
            device = $_.DeviceID
            total_bytes = [int64]$_.Size
            free_bytes = [int64]$_.FreeSpace
            used_percent = if ($_.Size) { [math]::Round((($_.Size - $_.FreeSpace) / $_.Size) * 100, 2) } else { 0 }
        }
        })
    }
    if ($Fields -contains 'services') {
        $payload.services = @(Get-Service | Where-Object {
        $_.StartType -eq 'Automatic' -and $_.Status -ne 'Running'
        } | Select-Object Name, DisplayName, Status, StartType)
    }
    if ($Fields -contains 'logs') {
        $payload.logs = @(Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2,3; StartTime=(Get-Date).AddHours(-24)} -MaxEvents 100 -ErrorAction SilentlyContinue | ForEach-Object {
        [ordered]@{time=$_.TimeCreated; id=$_.Id; provider=$_.ProviderName; level=$_.LevelDisplayName; message=$_.Message}
        })
    }
    if ($Fields -contains 'network_info') {
        $payload.network_info = @(Get-NetIPConfiguration | ForEach-Object {
        [ordered]@{
            interface = $_.InterfaceAlias
            ipv4 = @($_.IPv4Address.IPAddress)
            gateway = @($_.IPv4DefaultGateway.NextHop)
            dns = @($_.DNSServer.ServerAddresses)
        }
        })
    }
    return $payload
}

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        try {
            if ($context.Request.Url.AbsolutePath.TrimEnd('/') -ne '/inspection') {
                $context.Response.StatusCode = 404
                continue
            }
            if ($Token) {
                $expected = "Bearer $Token"
                if ($context.Request.Headers['Authorization'] -ne $expected) {
                    $context.Response.StatusCode = 401
                    continue
                }
            }
            $fieldSelection = $context.Request.QueryString['fields']
            if ($null -eq $fieldSelection) {
                $payload = Get-InspectionPayload
            } else {
                $fields = @($fieldSelection.Split(',') | Where-Object { $_ })
                $payload = Get-InspectionPayload -Fields $fields
            }
            $json = $payload | ConvertTo-Json -Depth 8 -Compress
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
            $context.Response.StatusCode = 200
            $context.Response.ContentType = 'application/json; charset=utf-8'
            $context.Response.ContentLength64 = $bytes.Length
            $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        } catch {
            $context.Response.StatusCode = 500
            $bytes = [System.Text.Encoding]::UTF8.GetBytes((@{error=$_.Exception.Message} | ConvertTo-Json -Compress))
            $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        } finally {
            $context.Response.OutputStream.Close()
        }
    }
} finally {
    $listener.Stop()
    $listener.Close()
}
