param(
    [int]$Port = 9180,
    [Parameter(Mandatory=$true)][string]$Token
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'InspectionHttpService.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "未找到服务脚本：$scriptPath"
}

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Port $Port -Token `"$Token`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName 'NetworkInspectionHttpService' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

$ruleName = "Network Inspection HTTP $Port"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
}

Start-ScheduledTask -TaskName 'NetworkInspectionHttpService'
Write-Host "安装完成。巡检地址：http://本机IP:$Port/inspection"
