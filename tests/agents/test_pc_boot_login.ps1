$ErrorActionPreference='Stop'
$agent=Join-Path $PSScriptRoot '../../net/scripts/templates/GetInfo_Upload.ps1'
$source=[IO.File]::ReadAllText($agent)
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$t,[ref]$e)
if($e.Count){throw 'Collector parse failed'}
$f=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Publish-PCDaily'},$true)
if($null -eq $f){throw 'Publisher missing'}
$body=$f.Extent.Text
foreach($required in @('latest.json','latest.lock','AllowAutoRedirect','Authorization','16MB','PC_UPLOAD_FAILED','GetRequestStream')){
 if($body -notmatch [regex]::Escape($required)){throw ('Missing API publisher safeguard: '+$required)}
}
foreach($obsolete in @('.done','CanPublish','CanPublishPayload','Shared folder','PC_ALREADY_UPLOADED')){
 if($body -match [regex]::Escape($obsolete)){throw ('Obsolete daily/share gate remains: '+$obsolete)}
}
Write-Output 'PASS API collector: local latest JSON, bounded bearer upload, no daily/login gate.'
