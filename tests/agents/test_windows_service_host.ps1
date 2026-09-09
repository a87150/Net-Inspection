param([ValidateSet('Lifecycle','StartupFailure','Crash')][string]$Mode='Lifecycle')
$ErrorActionPreference='Stop'
$path=Join-Path $PSScriptRoot '../../agents/server/windows/InspectionHttpService.ps1'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Script parse error' }
$source=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-InspectionServiceHostSource'},$true)
Invoke-Expression $source.Extent.Text
$root=Join-Path (Resolve-Path (Join-Path $PSScriptRoot '../..')) ('.task6-artifacts/host-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$target=Join-Path $root 'InspectionServiceHost.exe'
Add-Type -TypeDefinition (Get-InspectionServiceHostSource) -Language CSharp -ReferencedAssemblies 'System.ServiceProcess.dll' -OutputAssembly $target -OutputType WindowsApplication
# Fixture child only. No HTTP listener, OS service or firewall is created.
$childScript = 'param([switch]$RunService); [Console]::Out.WriteLine("Windows inspection HTTP service listening on port 9180"); Start-Sleep 60'
if ($Mode -eq 'StartupFailure') { $childScript='param([switch]$RunService); exit 9' }
if ($Mode -eq 'Crash') { $childScript='param([switch]$RunService); [Console]::Out.WriteLine("Windows inspection HTTP service listening on port 9180"); Start-Sleep 1; exit 9' }
[IO.File]::WriteAllText((Join-Path $root 'InspectionHttpService.ps1'),$childScript)
# Reflection runs the actual lifecycle code without registering with SCM.
# AppDomain base directory belongs to this test process, so set only the fixture root.
$assembly=[Reflection.Assembly]::LoadFile($target)
$type=$assembly.GetType('InspectionServiceHost')
$instance=[Activator]::CreateInstance($type)
$flags=[Reflection.BindingFlags]'Instance,NonPublic'
$type.GetField('root',$flags).SetValue($instance,($root+[IO.Path]::DirectorySeparatorChar))
$start=$type.GetMethod('OnStart',$flags);$stop=$type.GetMethod('OnStop',$flags)
[object[]]$arguments=,([string[]]@())
if ($Mode -eq 'StartupFailure') {
    try { $start.Invoke($instance,$arguments); throw 'Expected startup failure' }
    catch { if ($_.Exception.ToString() -notmatch 'Inspection listener did not start') { throw } }
    Write-Output 'PASS actual host rejects child startup failure.'
    exit 0
}
try {
    $start.Invoke($instance,$arguments)
    $child=$type.GetField('child',$flags).GetValue($instance)
    $childId=$child.Id
    if ($child.HasExited) { throw 'Child exited before readiness' }
    if ($Mode -eq 'Crash') { Start-Sleep 5; throw 'Host did not exit after child crash' }
    $stop.Invoke($instance,@())
    if (Get-Process -Id $childId -ErrorAction SilentlyContinue) { throw 'Stop left orphan child' }
    Write-Output 'PASS actual host compilation, readiness and child shutdown.'
} finally { $stop.Invoke($instance,@()); $instance.Dispose() }
