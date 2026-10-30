[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$OutputDirectory,
      [Parameter(Mandatory=$true)][string]$UserSid)
$ErrorActionPreference='Stop'
if($UserSid -notmatch '^S-1-5-21-\d+-\d+-\d+-1001$'){throw 'Use the reviewed Jude account SID.'}
$output=[IO.Path]::GetFullPath($OutputDirectory)
if($output.StartsWith('\\')){throw 'Use an ordinary local private build directory.'}
$private:forbidden=@('C:\Users\Jude\Documents\MSI-Project-Library',
    'C:\Users\Jude\AppData\Local\ProjectLibrary\Cache',
    (Join-Path $env:LOCALAPPDATA 'ProjectLibrary\Cache'))
foreach($private:path in $private:forbidden){
    $private:absolute=[IO.Path]::GetFullPath($private:path).TrimEnd('\')
    if($output.Equals($private:absolute,[StringComparison]::OrdinalIgnoreCase)-or $output.StartsWith($private:absolute+'\',[StringComparison]::OrdinalIgnoreCase)){
        throw 'Build output must stay outside the production kit and project cache.'
    }
}
$private:ancestor=$output
while($private:ancestor){
    if(Test-Path -LiteralPath $private:ancestor){
        if(([int][IO.File]::GetAttributes($private:ancestor)-band (0x400-bor 0x1000-bor 0x40000-bor 0x400000))-ne 0){throw 'Build paths must not contain links, cloud placeholders or offline files.'}
    }
    $private:ancestor=[IO.Path]::GetDirectoryName($private:ancestor)
}
if(Test-Path -LiteralPath $output){throw 'Use a new build directory.'}
[void][IO.Directory]::CreateDirectory($output)
$kit='C:\Users\Jude\Documents\MSI-Project-Library'
$device=Get-Content -LiteralPath (Join-Path $kit 'INTERLINKED-GUI-State\master\device.json') -Raw|ConvertFrom-Json
$exeHash=$device.helperPins.'HWINFO.exe';$dllHash=$device.helperPins.'HWiNFO64.dll'
foreach($pin in @($exeHash,$dllHash)){if($pin -cnotmatch '^[a-f0-9]{64}$'){throw 'Missing reviewed sensor pins.'}}
$workerPath=Join-Path $kit 'INTERLINKED-Desktop\INTERLINKED.Master.Worker.exe'
$appPath=Join-Path $kit 'INTERLINKED-App\interlinked_master.py'
$workerHash=(Get-FileHash -LiteralPath $workerPath).Hash.ToLowerInvariant()
$scheduler=New-Object -ComObject Schedule.Service
$scheduler.Connect()
$trccHash=''
try {
    $trcc=$scheduler.GetFolder('\').GetTask('TRCCAppStartup')
    if($trcc.Definition.Actions.Count -ne 1 -or $trcc.Definition.Triggers.Count -ne 1){throw 'Unknown TRCC startup action or trigger; review it first.'}
    $action=$trcc.Definition.Actions.Item(1);$trigger=$trcc.Definition.Triggers.Item(1);$principal=$trcc.Definition.Principal
    function AccountSid([string]$Identity){
        if($Identity.StartsWith('S-1-')){return ([Security.Principal.SecurityIdentifier]::new($Identity)).Value}
        return ([Security.Principal.NTAccount]::new($Identity)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    if($action.Type -ne 0 -or $action.Path.Trim('"') -ine 'C:\Program Files\TRCCCAP\TRCC.exe' -or $action.Arguments -or $action.WorkingDirectory -or
       (AccountSid $principal.UserId) -cne $UserSid -or $principal.LogonType -ne 3 -or $principal.RunLevel -ne 1 -or
       $trigger.Type -ne 9 -or $trigger.UserId){throw 'Unknown TRCC startup identity; review it first.'}
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$trccHash=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$trcc.Xml))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
} catch {
    $reason=$_.Exception;while($reason.InnerException){$reason=$reason.InnerException}
    if($reason -isnot [Runtime.InteropServices.COMException] -or $reason.HResult -ne -2147024894){throw}
}
$appHash=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot '..\app\interlinked_master.py')).Hash.ToLowerInvariant()
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$encoding=[Text.UTF8Encoding]::new($false)
$pins=Join-Path $output 'SensorPins.cs'
[IO.File]::WriteAllText($pins,"internal static partial class SensorStartup { const string UserSid=`"$UserSid`", ExeHash=`"$exeHash`", DllHash=`"$dllHash`"; }",$encoding)
$sensor=Join-Path $output 'INTERLINKED.Sensor.Startup.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ ('/out:'+$sensor) $pins (Join-Path $PSScriptRoot 'SensorStartup.cs') (Join-Path $PSScriptRoot 'SensorStartupNative.cs')
if($LASTEXITCODE -ne 0){throw 'Sensor startup compilation failed.'}
$sensorHash=(Get-FileHash -LiteralPath $sensor).Hash.ToLowerInvariant()
$startup=[ordered]@{format='interlinked-cooler-startup-v1';enabled=$true;machine='THIS-MACHINE';user='Jude';userSid=$UserSid;appSha256=$appHash;workerSha256=$workerHash;sensorSha256=$sensorHash}
$json=$startup|ConvertTo-Json -Compress
$setupPins=Join-Path $output 'SetupPins.cs'
$literal=$json.Replace('"','""')
[IO.File]::WriteAllText($setupPins,"internal static partial class CoolerStartupSetup { const string UserSid=`"$UserSid`", ExeHash=`"$exeHash`", DllHash=`"$dllHash`", WorkerHash=`"$workerHash`", AppHash=`"$appHash`", SensorHash=`"$sensorHash`", TrccTaskHash=`"$trccHash`", WorkerPath=@`"$workerPath`", AppPath=@`"$appPath`", StartupJson=@`"$literal`"; }",$encoding)
$manifest=Join-Path $output 'setup.manifest'
[IO.File]::WriteAllText($manifest,'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges><requestedExecutionLevel level="requireAdministrator" uiAccess="false"/></requestedPrivileges></security></trustInfo></assembly>',$encoding)
$setup=Join-Path $output 'INTERLINKED.Cooler.Startup.Setup.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ /reference:System.Windows.Forms.dll /reference:Microsoft.CSharp.dll ('/win32manifest:'+$manifest) ('/resource:'+$sensor+',sensor') ('/out:'+$setup) $setupPins (Join-Path $PSScriptRoot 'CoolerStartupSetup.cs')
if($LASTEXITCODE -ne 0){throw 'Startup setup compilation failed.'}
$startup.setupSha256=(Get-FileHash -LiteralPath $setup).Hash.ToLowerInvariant()
$startup.trccTaskSha256=$trccHash
[IO.File]::WriteAllText((Join-Path $output 'build.json'),($startup|ConvertTo-Json),$encoding)
$startup|ConvertTo-Json
