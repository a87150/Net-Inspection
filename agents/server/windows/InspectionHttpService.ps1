param(
    [ValidateRange(1, 65535)][int]$Port = 9180,
    [string]$Token = $env:NET_INSPECTION_AGENT_TOKEN,
    [switch]$Install,
    [switch]$Console,
    [switch]$RunService
)

$ErrorActionPreference = 'Stop'

function Invoke-InspectionWindowsPowerShell {
    param([string]$ScriptPath, [System.Collections.IDictionary]$Parameters)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This inspection agent requires Windows.'
    }
    $systemDirectory = if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) { 'Sysnative' } else { 'System32' }
    $engine = Join-Path ([Environment]::GetFolderPath('Windows')) "$systemDirectory\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $engine)) { throw 'Windows PowerShell is not installed; enable Windows PowerShell 5.1 and retry.' }
    $arguments = @{}
    foreach ($name in $Parameters.Keys) {
        if ($name -notin @('Port', 'Token', 'Install', 'Console', 'RunService')) { throw 'Unsupported agent argument.' }
        $value = $Parameters[$name]
        $arguments[$name] = if ($value -is [System.Management.Automation.SwitchParameter]) { [bool]$value } else { $value }
    }
    # Pipe the payload instead of putting the token in process arguments or temporary files.
    # Base64 makes the pipe independent of the two shells' console encodings.
    $payload = @{path=$ScriptPath; arguments=$arguments} | ConvertTo-Json -Depth 4 -Compress
    $command = @'
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())) | ConvertFrom-Json
    $parameters = @{}
    foreach ($property in $payload.arguments.PSObject.Properties) { $parameters[$property.Name] = $property.Value }
    $global:LASTEXITCODE = 0
    & ([string]$payload.path) @parameters
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine(("{0} (script line {1})" -f $_.Exception.Message, $_.InvocationInfo.ScriptLineNumber))
    exit 1
}
'@
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $engine
    $info.Arguments = '-NoLogo -NoProfile -NonInteractive -OutputFormat Text -ExecutionPolicy Bypass -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    # Process.Start does not apply PowerShell's native-command module-path cleanup.
    # This agent needs Windows inbox modules only; do not inherit Core/user modules.
    $info.EnvironmentVariables['PSModulePath'] = Join-Path ([Environment]::GetFolderPath('Windows')) 'System32\WindowsPowerShell\v1.0\Modules'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardError = $true
    $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'Cannot start Windows PowerShell.' }
        # Drain stderr concurrently so a verbose failure cannot fill the pipe and block the child.
        $errorRead = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payload)))
        $process.StandardInput.Close()
        $process.WaitForExit()
        $errorText = $errorRead.GetAwaiter().GetResult().Trim()
        if ($process.ExitCode -ne 0) {
            foreach ($secret in @([string]$Parameters['Token'], [string]$env:NET_INSPECTION_AGENT_TOKEN)) {
                if ($secret) { $errorText = $errorText.Replace($secret, '[REDACTED]') }
            }
            if (-not $errorText) { $errorText = 'Child process returned no error details; check the agent service.log.' }
            throw ("Windows PowerShell failed (exit {0}): {1}" -f $process.ExitCode, $errorText)
        }
        return $process.ExitCode
    } finally { $process.Dispose() }
}

if ($PSVersionTable.PSEdition -ne 'Desktop') {
    Write-Host 'Switching to Windows PowerShell for the inspection agent...'
    $childExitCode = Invoke-InspectionWindowsPowerShell -ScriptPath $PSCommandPath -Parameters $PSBoundParameters
    if ($childExitCode -ne 0) { throw "Windows PowerShell could not complete the operation (exit $childExitCode)." }
    return
}


function Get-InspectionServiceHostSource {
    return @"
using System;
using System.Diagnostics;
using System.IO;
using System.ServiceProcess;
using System.Threading;

public sealed class InspectionServiceHost : ServiceBase {
    private Process child;
    private volatile bool stopping;
    private volatile bool ready;
    private readonly ManualResetEvent started = new ManualResetEvent(false);
    private readonly object logLock = new object();
    private readonly string root = AppDomain.CurrentDomain.BaseDirectory;
    public InspectionServiceHost() {
        ServiceName = "NetworkInspectionHttpService";
        CanStop = true; CanShutdown = true; AutoLog = true;
    }
    private void Log(string message) {
        if (String.IsNullOrEmpty(message)) return;
        lock (logLock) {
            try {
                string file = Path.Combine(root, "service.log");
                if (File.Exists(file) && new FileInfo(file).Length > 2 * 1024 * 1024) {
                    File.Copy(file, file + ".previous", true); File.WriteAllText(file, "");
                }
                if (message.Length > 2048) message = message.Substring(0, 2048);
                File.AppendAllText(file, DateTime.UtcNow.ToString("o") + " " + message + Environment.NewLine);
            } catch { }
        }
    }
    protected override void OnStart(string[] args) {
        stopping = false; ready = false; started.Reset();
        var info = new ProcessStartInfo {
            FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                @"System32\WindowsPowerShell\v1.0\powershell.exe"),
            Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" +
                Path.Combine(root, "InspectionHttpService.ps1") + "\" -RunService",
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        child = new Process { StartInfo = info, EnableRaisingEvents = true };
        child.OutputDataReceived += (sender, e) => {
            Log(e.Data);
            if (e.Data != null && e.Data.StartsWith("Windows inspection HTTP service listening on port ")) {
                ready = true; started.Set();
            }
        };
        child.ErrorDataReceived += (sender, e) => Log(e.Data);
        child.Exited += (sender, e) => {
            started.Set();
            if (!stopping && ready) {
                Log("Inspection process exited unexpectedly; requesting service recovery.");
                Environment.Exit(1);
            }
        };
        child.Start(); child.BeginOutputReadLine(); child.BeginErrorReadLine();
        if (!started.WaitOne(20000) || !ready || child.HasExited) {
            OnStop();
            throw new InvalidOperationException("Inspection listener did not start. Check service.log and port ownership.");
        }
    }
    protected override void OnStop() {
        stopping = true;
        if (child != null) {
            try { if (!child.HasExited) { child.Kill(); child.WaitForExit(5000); } } catch { }
            child.Dispose(); child = null;
        }
    }
    protected override void OnShutdown() { OnStop(); }
    public static void Main() { ServiceBase.Run(new InspectionServiceHost()); }
}
"@
}

function Set-InspectionDirectoryAccess {
    param([string]$Directory)
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    $acl.SetOwner((New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    Set-Acl -LiteralPath $Directory -AclObject $acl
}

function Assert-InspectionAdministrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Please run Windows PowerShell as administrator to install this service.'
    }
    if ($PSVersionTable.PSEdition -ne 'Desktop') { throw 'Run this script with Windows PowerShell 5.1 (powershell.exe).' }
}

function Invoke-InspectionServiceControl {
    param([string[]]$Arguments)
    & "$env:SystemRoot\System32\sc.exe" @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ('Windows service configuration failed: ' + $Arguments[0]) }
}

function Install-InspectionService {
    param([string]$Source, [string]$Directory, [ValidateRange(1,65535)][int]$ListenPort, [string]$AgentToken)
    Assert-InspectionAdministrator
    if (-not $AgentToken) { throw 'First installation requires -Token matching the token configured in the Web application.' }
    $serviceName = 'NetworkInspectionHttpService'
    $hostPath = Join-Path $Directory 'InspectionServiceHost.exe'
    $scriptPath = Join-Path $Directory 'InspectionHttpService.ps1'
    $configPath = Join-Path $Directory 'settings.json'
    $marker = Join-Path $Directory '.inspection-service'
    $mutex = New-Object System.Threading.Mutex($false, 'Global\NetworkInspectionHttpServiceInstall')
    $locked = $false
    $stagedHost = $null
    $previous = @{}
    $changed = $false
    $createdService = $false
    $createdFirewall = $false
    $previousFirewallPorts = $null
    $legacyTask = $null
    $wasRunning = $false
    $previousAcl = $null
    $existing = $null
    try {
        try { $locked = $mutex.WaitOne(0) }
        catch [System.Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw 'Another inspection service installation is in progress.' }
        if (Test-Path -LiteralPath $Directory) {
            if ((Get-Item -LiteralPath $Directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installation directory cannot be a link.' }
            if (-not (Test-Path -LiteralPath $marker) -and @(Get-ChildItem -LiteralPath $Directory -Force).Count) { throw 'Refusing to overwrite an unowned installation directory.' }
            if ((Test-Path -LiteralPath $marker) -and [IO.File]::ReadAllText($marker) -ne 'NetworkInspectionHttpService/v1') { throw 'Unknown installation marker; refusing to overwrite files.' }
            $previousAcl = Get-Acl -LiteralPath $Directory
        } else { New-Item -ItemType Directory -Path $Directory | Out-Null }
        Set-InspectionDirectoryAccess $Directory
        foreach ($file in @($hostPath, $scriptPath, $configPath, $marker)) {
            if (Test-Path -LiteralPath $file) {
                if ((Get-Item -LiteralPath $file -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installation files cannot be links.' }
                $previous[$file] = [IO.File]::ReadAllBytes($file)
            }
        }
        $existing = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
        if ($existing -and $existing.PathName.Trim('"') -ne $hostPath) { throw 'An unrelated Windows service already uses this service name.' }
        if ($existing -and ($existing.StartName -ne 'LocalSystem' -or $existing.ServiceType -ne 'Own Process' -or $existing.State -notin @('Running','Stopped'))) {
            throw 'Existing service must be LocalSystem, own-process and running/stopped; refusing to alter another service identity.'
        }
        $wasRunning = $existing -and $existing.State -eq 'Running'
        $stagedHost = Join-Path $Directory (([guid]::NewGuid().ToString('N')) + '.exe')
        Add-Type -TypeDefinition (Get-InspectionServiceHostSource) -Language CSharp -ReferencedAssemblies 'System.ServiceProcess.dll' -OutputAssembly $stagedHost -OutputType WindowsApplication
        # Preserve an earlier scheduled task, but disable its duplicate startup.
        $legacyTask = Get-ScheduledTask -TaskPath '\' -TaskName $serviceName -ErrorAction SilentlyContinue
        if ($legacyTask -and -not (@($legacyTask.Actions | Where-Object { $_.Arguments -like '*InspectionHttpService.ps1*' }).Count)) { throw 'An unrelated scheduled task uses this name.' }
        if (-not $wasRunning -and (-not $legacyTask -or $legacyTask.State -ne 'Running') -and
            (Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue)) {
            throw 'The inspection port is already in use. Stop the old console script before installing.'
        }
        if ($legacyTask) {
            Stop-ScheduledTask -TaskPath '\' -TaskName $serviceName
            Disable-ScheduledTask -TaskPath '\' -TaskName $serviceName | Out-Null
        }
        if ($wasRunning) {
            Stop-Service -Name $serviceName
            (Get-Service -Name $serviceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(20))
        }
        if (Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue) {
            if ($wasRunning) { Start-Service -Name $serviceName }
            throw 'The inspection port remains occupied after stopping the previous agent.'
        }
        $changed = $true
        [IO.File]::WriteAllText($marker, 'NetworkInspectionHttpService/v1')
        $sourceBytes = [IO.File]::ReadAllBytes($Source)
        [IO.File]::WriteAllBytes($scriptPath, $sourceBytes)
        [IO.File]::WriteAllBytes($hostPath, [IO.File]::ReadAllBytes($stagedHost))
        [IO.File]::WriteAllText($configPath, (@{port=$ListenPort; token=$AgentToken} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
        if (-not $existing) {
            New-Service -Name $serviceName -DisplayName 'Network Inspection HTTP Agent' -BinaryPathName ('"' + $hostPath + '"') -StartupType Automatic -Description 'Read-only Windows inspection HTTP endpoint.' | Out-Null
            $createdService = $true
            Invoke-InspectionServiceControl -Arguments @('failure', $serviceName, 'reset=', '86400', 'actions=', 'restart/5000/restart/15000/restart/60000')
            Invoke-InspectionServiceControl -Arguments @('failureflag', $serviceName, '1')
        } elseif ($existing.StartMode -eq 'Disabled') { Set-Service -Name $serviceName -StartupType Manual }
        if (-not (Get-NetFirewallRule -Name $serviceName -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -Name $serviceName -DisplayName 'Network Inspection HTTP Agent' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $ListenPort -Profile Domain,Private | Out-Null
            $createdFirewall = $true
        } else {
            $previousFirewallPorts = (Get-NetFirewallRule -Name $serviceName | Get-NetFirewallPortFilter).LocalPort
            Get-NetFirewallRule -Name $serviceName | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort $ListenPort | Out-Null
        }
        Start-Service -Name $serviceName
        (Get-Service -Name $serviceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(25))
        Set-Service -Name $serviceName -StartupType Automatic
        Write-Host "Installed and started $serviceName (Automatic, LocalSystem), port $ListenPort."
        Write-Host "Configuration and bounded logs: $Directory"
    } catch {
        $failure = $_
        if ($changed) {
            Stop-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($createdService) { Invoke-InspectionServiceControl -Arguments @('delete', $serviceName) }
            foreach ($file in @($hostPath, $scriptPath, $configPath, $marker)) {
                if ($previous.ContainsKey($file)) { [IO.File]::WriteAllBytes($file, $previous[$file]) }
                elseif ($file -eq $marker) { [IO.File]::WriteAllText($marker, 'NetworkInspectionHttpService/v1') }
                elseif (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
            }
            if ($existing) {
                $oldStartup = @{Auto='Automatic'; Manual='Manual'; Disabled='Disabled'}[$existing.StartMode]
                if ($oldStartup) { Set-Service -Name $serviceName -StartupType $oldStartup }
            }
            if ($wasRunning) { Start-Service -Name $serviceName -ErrorAction Continue }
        }
        if ($previousFirewallPorts) { Get-NetFirewallRule -Name $serviceName | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort $previousFirewallPorts | Out-Null }
        if ($createdFirewall) { Remove-NetFirewallRule -Name $serviceName -ErrorAction SilentlyContinue }
        if ($legacyTask -and $legacyTask.Settings.Enabled) {
            Enable-ScheduledTask -TaskPath '\' -TaskName $serviceName -ErrorAction Continue | Out-Null
            if ($legacyTask.State -eq 'Running') { Start-ScheduledTask -TaskPath '\' -TaskName $serviceName -ErrorAction Continue }
        }
        if ($previousAcl) { Set-Acl -LiteralPath $Directory -AclObject $previousAcl }
        throw $failure
    } finally {
        if ($stagedHost -and (Test-Path -LiteralPath $stagedHost)) { Remove-Item -LiteralPath $stagedHost }
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

$installDirectory = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'NetworkInspectionAgent'
if ($Console -and ($Install -or $RunService)) { throw 'Choose either console mode or service mode.' }
if ($RunService) {
    try { $settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'settings.json') -Raw | ConvertFrom-Json }
    catch { throw 'Cannot read service settings.json; check the protected configuration file.' }
    $Port = [int]$settings.port
    $Token = [string]$settings.token
    if ($Port -lt 1 -or $Port -gt 65535 -or -not $Token) { throw 'Invalid service configuration.' }
} elseif (-not $Console) {
    Assert-InspectionAdministrator
    $savedSettings = Join-Path $installDirectory 'settings.json'
    if (Test-Path -LiteralPath $savedSettings) {
        try { $settings = Get-Content -LiteralPath $savedSettings -Raw | ConvertFrom-Json }
        catch { throw 'Cannot read saved settings.json; check the protected configuration file.' }
        if (-not $PSBoundParameters.ContainsKey('Port')) { $Port = [int]$settings.port }
        if (-not $Token) { $Token = [string]$settings.token }
    }
    Install-InspectionService -Source $PSCommandPath -Directory $installDirectory -ListenPort $Port -AgentToken $Token
    return
}

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://+:$Port/")
$listener.Start()
[Console]::Out.WriteLine("Windows inspection HTTP service listening on port $Port")

function Add-CollectionError {
    param(
        [System.Collections.IDictionary]$Errors,
        [string]$Field,
        $ErrorRecord
    )
    $message = if ($ErrorRecord -is [System.Management.Automation.ErrorRecord]) {
        $ErrorRecord.Exception.Message
    } elseif ($ErrorRecord -is [System.Exception]) {
        $ErrorRecord.Message
    } else {
        [string]$ErrorRecord
    }
    $message = ($message -replace '[\r\n]+', ' ').Trim()
    if ($Token) { $message = $message.Replace($Token, '[REDACTED]') }
    if ($message.Length -gt 500) { $message = $message.Substring(0, 500) + '…' }
    if ($message) { $Errors[$Field] = @($Errors[$Field] | Where-Object { $_ }) + $message }
}

function Get-InspectionPayload {
    param([string[]]$Fields = @('computer_name', 'system_info', 'cpu', 'memory', 'storage_status', 'network_info', 'services', 'logs'))
    $payload = [ordered]@{ collected_at = (Get-Date).ToString('o') }
    $collectionErrors = [ordered]@{}
    if ($Fields -contains 'computer_name') { $payload.computer_name = $env:COMPUTERNAME }

    $os = $null
    if (($Fields -contains 'system_info') -or ($Fields -contains 'memory')) {
        try {
            $os = Get-CimInstance Win32_OperatingSystem
        } catch {
            if ($Fields -contains 'system_info') { Add-CollectionError $collectionErrors 'system_info' $_ }
            if ($Fields -contains 'memory') { Add-CollectionError $collectionErrors 'memory' $_ }
        }
    }
    if (($Fields -contains 'system_info') -and $os) {
        try {
            $payload.system_info = [ordered]@{
                caption = $os.Caption
                version = $os.Version
                build_number = $os.BuildNumber
                architecture = $os.OSArchitecture
                last_boot_time = $os.LastBootUpTime
                uptime_seconds = [int64]((Get-Date) - $os.LastBootUpTime).TotalSeconds
            }
        } catch { Add-CollectionError $collectionErrors 'system_info' $_ }
    }
    if ($Fields -contains 'cpu') {
        try {
            $processors = @(Get-CimInstance Win32_Processor)
            $cpuLoad = ($processors | Measure-Object -Property LoadPercentage -Average).Average
            if ($null -eq $cpuLoad) { throw 'CPU load information is unavailable' }
            $payload.cpu = [ordered]@{usage_percent=[math]::Round([double]$cpuLoad, 2); logical_processors=($processors.NumberOfLogicalProcessors | Measure-Object -Sum).Sum; physical_cores=($processors.NumberOfCores | Measure-Object -Sum).Sum; model=(@($processors.Name | Where-Object { $_ } | Select-Object -Unique) -join "; ")}
        } catch { Add-CollectionError $collectionErrors 'cpu' $_ }
    }
    if (($Fields -contains 'memory') -and $os) {
        try {
            $payload.memory = [ordered]@{
                total_bytes = [int64]$os.TotalVisibleMemorySize * 1024
                free_bytes = [int64]$os.FreePhysicalMemory * 1024
                used_percent = [math]::Round((1 - ($os.FreePhysicalMemory / $os.TotalVisibleMemorySize)) * 100, 2)
            }
        } catch { Add-CollectionError $collectionErrors 'memory' $_ }
    }
    if ($Fields -contains 'storage_status') {
        try {
            $payload.storage_status = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
                [ordered]@{
                    device = $_.DeviceID
                    total_bytes = [int64]$_.Size
                    free_bytes = [int64]$_.FreeSpace
                    used_percent = if ($_.Size) { [math]::Round((($_.Size - $_.FreeSpace) / $_.Size) * 100, 2) } else { 0 }
                }
            })
        } catch { Add-CollectionError $collectionErrors 'storage_status' $_ }
        try {
            # Disk devices, not logical partitions; virtual machines report their virtual disks.
            $payload.physical_disks = @(Get-CimInstance Win32_DiskDrive | ForEach-Object {
                [ordered]@{device=$_.DeviceID; total_bytes=$_.Size}
            })
        } catch {
            # Missing inventory must not discard usable volume utilization evidence.
            $payload.physical_disks = $null
        }
    }
    if ($Fields -contains 'services') {
        $payload.services = @()
        $serviceErrors = @()
        try {
            $serviceResults = @(Get-Service -ErrorAction Continue 2>&1)
            $serviceErrors = @($serviceResults | Where-Object { ($_ -is [System.Management.Automation.ErrorRecord]) -or ($_ -is [System.Exception]) })
            $services = @($serviceResults | Where-Object { -not (($_ -is [System.Management.Automation.ErrorRecord]) -or ($_ -is [System.Exception])) })
            foreach ($service in $services) {
                try {
                    if ($service.StartType -eq 'Automatic' -and $service.Status -ne 'Running') {
                        $payloadServices = @($payload.services)
                        $payload.services = $payloadServices + [ordered]@{
                            Name = $service.Name
                            DisplayName = $service.DisplayName
                            Status = $service.Status
                            StartType = $service.StartType
                        }
                    }
                } catch { Add-CollectionError $collectionErrors 'services' $_ }
            }
        } catch { Add-CollectionError $collectionErrors 'services' $_ }
        foreach ($serviceError in $serviceErrors) { Add-CollectionError $collectionErrors 'services' $serviceError }
        if (-not $payload.Contains('services')) { $payload.services = @() }
    }
    if ($Fields -contains 'logs') {
        $logErrors = @()
        try {
            $events = @(Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2,3; StartTime=(Get-Date).AddHours(-24)} -MaxEvents 100 -ErrorAction SilentlyContinue -ErrorVariable +logErrors)
            $payload.logs = @($events | ForEach-Object {
                [ordered]@{time=$_.TimeCreated; id=$_.Id; provider=$_.ProviderName; level=$_.LevelDisplayName; message=$_.Message}
            })
        } catch {
            if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { Add-CollectionError $collectionErrors 'logs' $_ }
        }
        foreach ($logError in $logErrors) {
            if ($logError.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { Add-CollectionError $collectionErrors 'logs' $logError }
        }
        if (-not $payload.Contains('logs')) { $payload.logs = @() }
    }
    if ($Fields -contains 'network_info') {
        try {
            $payload.network_info = @(Get-NetIPConfiguration | ForEach-Object {
                [ordered]@{
                    interface = $_.InterfaceAlias
                    ipv4 = @($_.IPv4Address.IPAddress)
                    gateway = @($_.IPv4DefaultGateway.NextHop)
                    dns = @($_.DNSServer.ServerAddresses)
                }
            })
        } catch { Add-CollectionError $collectionErrors 'network_info' $_ }
    }
    if ($collectionErrors.Count) { $payload.collection_errors = $collectionErrors }
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
            # A disconnected client must not stop the listener.
            $failure = [ordered]@{}
            Add-CollectionError $failure 'request' $_
            try {
                $context.Response.StatusCode = 500
                $bytes = [System.Text.Encoding]::UTF8.GetBytes((@{error=($failure.request -join '; ')} | ConvertTo-Json -Compress))
                $context.Response.ContentType = 'application/json; charset=utf-8'
                $context.Response.ContentLength64 = $bytes.Length
                $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
            } catch { }
        } finally {
            try { $context.Response.OutputStream.Close() } catch { }
        }
    }
} finally {
    $listener.Stop()
    $listener.Close()
}
