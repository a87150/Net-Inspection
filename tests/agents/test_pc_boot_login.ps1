$ErrorActionPreference='Stop'
$agent=Join-Path $PSScriptRoot '../../net/scripts/templates/GetInfo_Upload.ps1'
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($agent),[ref]$t,[ref]$e)
if($e.Count){throw 'Collector parse failed'}
$f=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Publish-PCDaily'},$true)
Invoke-Expression $f.Extent.Text
$entry=$ast.EndBlock.Statements[-1].PipelineElements[0]
$validator=$entry.CommandElements[-1].ScriptBlock.GetScriptBlock()
if(-not (& $validator @{'系统信息概览'=@{'当前登录用户工号'='DOMAIN\fixture'}})){throw 'Real payload identity gate rejected a logged-in user'}
if(& $validator @{'系统信息概览'=@{'当前登录用户工号'=''}}){throw 'Real payload identity gate accepted an empty user'}
$root=Join-Path ([IO.Path]::GetTempPath()) ('pc-boot-login-'+[guid]::NewGuid().ToString('N'))
try {
 $share=Join-Path $root 'share';$state=Join-Path $root 'state';[IO.Directory]::CreateDirectory($share)|Out-Null
 Publish-PCDaily $share $state 'TEST-PC' {throw 'must wait'} -CanPublish {$false}
 if(@(Get-ChildItem -LiteralPath $share).Count -or @(Get-ChildItem -LiteralPath $state -Filter '*.done' -ErrorAction SilentlyContinue).Count){throw 'No-login run completed the day'}
 Publish-PCDaily $share $state 'TEST-PC' {@{user=''}} -CanPublish {$true} -CanPublishPayload {param($payload) -not [string]::IsNullOrWhiteSpace($payload.user)}
 if(@(Get-ChildItem -LiteralPath $share).Count -or @(Get-ChildItem -LiteralPath $state -Filter '*.done' -ErrorAction SilentlyContinue).Count){throw 'Logout during collection completed the day'}
 Publish-PCDaily $share $state 'TEST-PC' {@{platform='windows';user='fixture'}} -CanPublish {$true} -CanPublishPayload {param($payload) -not [string]::IsNullOrWhiteSpace($payload.user)}
 if(@(Get-ChildItem -LiteralPath $share -Filter '*.json').Count -ne 1 -or @(Get-ChildItem -LiteralPath $state -Filter '*.done').Count -ne 1){throw 'Interactive login did not complete once'}
 try {Publish-PCDaily $share $state '' {@{}};throw 'expected empty name failure'} catch {if($_.Exception.Message -notlike '*computer name*'){throw}}
 Write-Output 'PASS boot login: no-login waits without marker; first login completes once; empty device name rejected.'
} finally {if(Test-Path -LiteralPath $root){Remove-Item -LiteralPath $root -Recurse -Force}}