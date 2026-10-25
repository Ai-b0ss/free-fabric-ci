#Requires -Version 5.1
<#
  Antigravity health check / conditional repair for Windows.
  Detect -> live test -> classify -> optional QNIX local eligibility patch
  -> live retest -> report. Never handles credentials or changes VPN config.

  Default: diagnostics only.
  -ApplyIfNeeded: patch only when the CLI reports a CLIENT-SIDE eligibility failure.
  -DesktopEligibilityBlocked: explicit operator assertion that Desktop shows a
    LOCAL eligibility error; can patch Desktop even without a usable CLI test.
  Pinned reviewed commit: source digest is independently verified before any patch
    code is executed. Updating requires a reviewed source + matching digest.
#>
[CmdletBinding()]
param(
    [switch]$ApplyIfNeeded,
    [switch]$DesktopEligibilityBlocked,
    [switch]$InstallPythonIfMissing,
    [string]$Model = '',
    [ValidateRange(15,300)]
    [int]$ModelTimeoutSeconds = 75
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$marker = 'ANTIGRAVITY_HEALTHCHECK_OK'
$started = Get-Date
$work = Join-Path $env:LOCALAPPDATA 'Antigravity-Health-Repair'
$logPath = Join-Path $work 'last-run.txt'
$script:Events = New-Object 'System.Collections.Generic.List[string]'
$script:NewPatches = @()
$script:PythonFile = $null
$script:PythonPrefix = @()
$script:Patcher = $null
$script:CLI = $null
$script:Desktop = $null
$script:Manager = $null
$script:Before = $null
$script:After = $null
$script:Conclusion = 'UNKNOWN'
$script:PatcherRef = '90498f50353d414232f593dffe88899bc80b6312'
# GitHub API returned this blob SHA-1 for manager.py at the full pinned commit.
# It hashes the Git object (ASCII 'blob <length>\0' + the raw file bytes).
$script:ExpectedPatcherBlobSha1 = '99c61225c78ba3b66eca71fb0c0e278d1bf0bf27'

function Say([string]$code, [string]$details='') {
    $line = ('{0,-28} {1}' -f $code,$details)
    Write-Host $line
    $script:Events.Add($line)
}
function Finish([string]$code, [string]$details='', [int]$exitCode=1) {
    $script:Conclusion = $code
    Say ('FINAL: ' + $code) $details
    Say 'ELAPSED_SECONDS' ([math]::Round(((Get-Date)-$started).TotalSeconds,1))
    try {
        [void](New-Item -ItemType Directory -Path $work -Force)
        $script:Events | Set-Content -LiteralPath $logPath -Encoding UTF8
        Write-Host ('LOG: ' + $logPath)
    } catch { Write-Warning ('Could not write log: ' + $_.Exception.Message) }
    exit $exitCode
}
function FirstExisting([string[]]$paths) {
    foreach($p in $paths) {
        if($p -and (Test-Path -LiteralPath $p -PathType Leaf)) {
            try { return (Resolve-Path -LiteralPath $p).Path } catch {}
        }
    }
    return $null
}
function FindPrograms {
    $cliPaths = New-Object 'System.Collections.Generic.List[string]'
    if($env:LOCALAPPDATA) { $cliPaths.Add((Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe')) }
    $cmd = Get-Command agy.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if($cmd -and $cmd.Source) { $cliPaths.Add($cmd.Source) }
    $script:CLI = FirstExisting ($cliPaths.ToArray())

    $appPaths = New-Object 'System.Collections.Generic.List[string]'
    if($env:LOCALAPPDATA) { $appPaths.Add((Join-Path $env:LOCALAPPDATA 'Programs\antigravity\Antigravity.exe')) }
    if($env:ProgramFiles) { $appPaths.Add((Join-Path $env:ProgramFiles 'Antigravity\Antigravity.exe')) }
    $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if($pf86) { $appPaths.Add((Join-Path $pf86 'Antigravity\Antigravity.exe')) }
    $appCmd = Get-Command antigravity.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if($appCmd -and $appCmd.Source) { $appPaths.Add($appCmd.Source) }

    # Search uninstall records without a recursive disk crawl.
    $registry = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
    foreach($item in @(Get-ItemProperty -Path $registry -ErrorAction SilentlyContinue)) {
        if($item.DisplayName -notmatch '(?i)antigravity') { continue }
        if($item.InstallLocation) { $appPaths.Add((Join-Path ([string]$item.InstallLocation) 'Antigravity.exe')) }
        if($item.DisplayIcon) {
            $icon = [string]$item.DisplayIcon
            if($icon -match '^(?:"([^"]+\.exe)"|([^,]+\.exe))(?:,.*)?$') {
                $candidate = if($matches[1]) { $matches[1] } else { $matches[2] }
                if($candidate) { $appPaths.Add($candidate.Trim()) }
            }
        }
    }
    $script:Desktop = FirstExisting ($appPaths.ToArray())
    if($script:Desktop) {
        $ls = Join-Path (Split-Path -Parent $script:Desktop) 'resources\bin\language_server.exe'
        $script:Manager = FirstExisting @($ls)
    }
}
function QuoteArg([string]$s) {
    if($s -notmatch '[\s"]') { return $s }
    # Inputs are known arguments and local paths (Windows paths cannot contain ").
    return '"' + $s.Replace('"','\"') + '"'
}
function Execute([string]$file, [string[]]$arguments, [int]$timeoutSeconds=30) {
    $out = Join-Path $env:TEMP ('agycheck-' + [guid]::NewGuid().ToString('N') + '.out')
    $err = Join-Path $env:TEMP ('agycheck-' + [guid]::NewGuid().ToString('N') + '.err')
    $argsText = ($arguments | ForEach-Object { QuoteArg ([string]$_) }) -join ' '
    try {
        $proc = Start-Process -FilePath $file -ArgumentList $argsText -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err -ErrorAction Stop
        if(-not $proc.WaitForExit($timeoutSeconds * 1000)) {
            try { $proc.Kill() } catch {}
            try { [void]$proc.WaitForExit(2500) } catch {}
            return [pscustomobject]@{ExitCode=124; Stdout=''; Stderr=''; TimedOut=$true}
        }
        $stdout = if(Test-Path $out) { [IO.File]::ReadAllText($out) } else { '' }
        $stderr = if(Test-Path $err) { [IO.File]::ReadAllText($err) } else { '' }
        return [pscustomobject]@{ExitCode=$proc.ExitCode;Stdout=$stdout;Stderr=$stderr;TimedOut=$false}
    } finally {
        Remove-Item -LiteralPath $out,$err -Force -ErrorAction SilentlyContinue
    }
}
function LiveTest {
    if(-not $script:CLI) { return [pscustomobject]@{State='NO_CLI'; ExitCode=-1} }
    $testArguments=@('--print',('Reply with EXACTLY this text: ' + $marker),
                     '--print-timeout',($ModelTimeoutSeconds.ToString()+'s'),'--output-format','json')
    if($Model) { $testArguments+=@('--model',$Model) }
    $r = Execute $script:CLI $testArguments ($ModelTimeoutSeconds + 12)
    $body = $r.Stdout + "`n" + $r.Stderr
    $parsed = $null
    try { $parsed = $r.Stdout | ConvertFrom-Json -ErrorAction Stop } catch {}
    if($r.ExitCode -eq 0 -and $parsed -and $parsed.status -eq 'SUCCESS' -and
       ([string]$parsed.response).Trim() -eq $marker) {
        return [pscustomobject]@{State='PASS';ExitCode=$r.ExitCode}
    }
    if($r.TimedOut) { return [pscustomobject]@{State='TIMEOUT';ExitCode=124} }
    if($body -match '(?i)please sign in|login required|not logged in|authenticate|authorization required|unauthorized|token expired') {
        return [pscustomobject]@{State='AUTH_REQUIRED';ExitCode=$r.ExitCode}
    }
    # Google's HTTP 400 region denial is SERVER-SIDE, not cured by client binary changes.
    if($body -match '(?i)user location is not supported|unsupported.location|country.not.supported|unsupported.region|HTTP\s*400.{0,120}(?:location|region)') {
        return [pscustomobject]@{State='SERVER_REGION_DENIED';ExitCode=$r.ExitCode}
    }
    if($body -match '(?i)eligibility.check|ineligible|local eligibility|not eligible|hasValidAuth') {
        return [pscustomobject]@{State='CLIENT_ELIGIBILITY';ExitCode=$r.ExitCode}
    }
    if($body -match '(?i)quota|rate.limit|429|resource.exhausted') {
        return [pscustomobject]@{State='QUOTA_OR_RATE_LIMIT';ExitCode=$r.ExitCode}
    }
    return [pscustomobject]@{State='UNKNOWN_FAILURE';ExitCode=$r.ExitCode}
}
function NetworkAudit {
    foreach($family in @('-4','-6')) {
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if(-not $curl) { Say ('GEO_'+$family) 'curl.exe unavailable';continue }
        try {
            $x = Execute $curl.Source @($family,'--silent','--show-error','--max-time','12','https://ipwho.is/') 16
            $obj = $x.Stdout | ConvertFrom-Json -ErrorAction Stop
            $country = [string]$obj.country_code
            if(-not $country) { $country='UNKNOWN' }
            Say ('GEO_'+$family) $country
        } catch { Say ('GEO_'+$family) 'Not determined' }
    }
    Say 'GEO_NOTE' 'Observed egress only; country does not prove Google model access.'
}
function DesktopLiveTest([switch]$CloseIfStarted) {
    if(-not $script:Desktop) {return [pscustomobject]@{State='NO_DESKTOP';Reason='Desktop not installed'} }
    $tester=Join-Path $PSScriptRoot 'Test-Desktop.ps1'
    if(-not (Test-Path -LiteralPath $tester)) { return [pscustomobject]@{State='UNVERIFIED';Reason='Test-Desktop.ps1 is missing'} }
    try {
        if($CloseIfStarted) {
            $result=& $tester -ExePath $script:Desktop -TimeoutSeconds $ModelTimeoutSeconds -CloseIfStarted
        } else {
            $result=& $tester -ExePath $script:Desktop -TimeoutSeconds $ModelTimeoutSeconds
        }
        if($null -ne $result -and $result.State) {return $result}
        return [pscustomobject]@{State='UNVERIFIED';Reason='Desktop tester returned no result'}
    } catch {
        return [pscustomobject]@{State='UNVERIFIED';Reason=$_.Exception.Message}
    }
}
function FindPython {
    $choices = @()
    $p = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if($p) { $choices += ,@($p.Source,'-3') }
    $p = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if($p) { $choices += ,@($p.Source) }
    foreach($c in $choices) {
        $file=[string]$c[0]
        $pre=@(); if($c.Count -gt 1) { $pre=@($c[1..($c.Count-1)]) }
        try {
            $r=Execute $file ($pre+@('-c','import sys; print(sys.version_info.major)')) 12
            if($r.ExitCode -eq 0 -and $r.Stdout.Trim() -eq '3') {
                $script:PythonFile=$file; $script:PythonPrefix=$pre
                return $true
            }
        } catch {}
    }
    if(-not $InstallPythonIfMissing) { return $false }
    $winget=Get-Command winget.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if(-not $winget) { Say 'PYTHON_INSTALL' 'WinGet unavailable'; return $false }
    Say 'PYTHON_INSTALL' 'Attempting official Python.Python.3.13 via WinGet'
    try {
        $inst=Execute $winget.Source @('install','--id','Python.Python.3.13','-e','--source','winget',
            '--silent','--disable-interactivity','--accept-package-agreements','--accept-source-agreements') 240
        Say 'PYTHON_INSTALL_EXIT' ([string]$inst.ExitCode)
    } catch { Say 'PYTHON_INSTALL_FAILED' $_.Exception.Message;return $false }
    $installedPaths = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:ProgramFiles 'Python313\python.exe')
    )
    foreach($path in $installedPaths) {
        if(-not (Test-Path $path -PathType Leaf)) { continue }
        try {
            $r=Execute $path @('-c','import sys; print(sys.version_info.major)') 15
            if($r.ExitCode -eq 0 -and $r.Stdout.Trim() -eq '3') {
                $script:PythonFile=$path;$script:PythonPrefix=@();return $true
            }
        } catch {}
    }
    return $false
}
function PythonPatcher([string[]]$argsIn,[int]$timeout=35) {
    return Execute $script:PythonFile ($script:PythonPrefix+@($script:Patcher)+$argsIn) $timeout
}
function FetchPinnedPatcher {
    [void](New-Item -ItemType Directory -Path $work -Force)
    $src = Join-Path $work ('manager-'+$script:PatcherRef+'.py')
    # Always fetch immutable Git-commit source fresh: do not execute a previously
    # cached, possibly modified manager.py file from a writable local directory.
    $url = 'https://raw.githubusercontent.com/QNIX-Dev/eligibility-antigravity-patcher/' + $script:PatcherRef + '/manager.py'
    $tmp = $src + '.part'
    try {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 25 -ErrorAction Stop
        if((Get-Item -LiteralPath $tmp).Length -lt 15000) { throw 'Downloaded source is unexpectedly small' }
        $beginning = [IO.File]::ReadAllText($tmp).Substring(0, [Math]::Min(1500,(Get-Item $tmp).Length))
        if($beginning -notmatch 'Patch Antigravity|Antigravity eligibility') { throw 'Unexpected Python source header' }
        # A full commit hash by itself is not sufficient validation when the
        # executable source is downloaded from the internet: verify the exact
        # Git blob fingerprint obtained independently from GitHub's API.
        $sourceBytes = [IO.File]::ReadAllBytes($tmp)
        $prefix = [Text.Encoding]::ASCII.GetBytes(('blob ' + $sourceBytes.Length + [char]0))
        $blob = New-Object 'byte[]' ($prefix.Length + $sourceBytes.Length)
        [Array]::Copy($prefix,0,$blob,0,$prefix.Length)
        [Array]::Copy($sourceBytes,0,$blob,$prefix.Length,$sourceBytes.Length)
        $gitBlobHash = [BitConverter]::ToString(([Security.Cryptography.SHA1]::Create()).ComputeHash($blob)).Replace('-','').ToLowerInvariant()
        if($gitBlobHash -ne $script:ExpectedPatcherBlobSha1) {
            throw ('Patcher integrity check failed: expected '+$script:ExpectedPatcherBlobSha1+', got '+$gitBlobHash)
        }
        Say 'PATCHER_GIT_BLOB_VERIFIED' $gitBlobHash
        Move-Item -LiteralPath $tmp -Destination $src -Force
    } catch {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        throw ('Could not download pinned QNIX source: '+$_.Exception.Message)
    }
    $script:Patcher = $src
    Say 'PATCHER_SOURCE' ('QNIX commit '+$script:PatcherRef)
    Say 'PATCHER_SHA256' (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
}
function TargetArgs([string]$kind, [string]$command) {
    if($kind -eq 'cli') { return @($command,'cli','--path-cli',$script:CLI) }
    return @($command,'manager','--path-manager',$script:Manager)
}
function StatusOf([string]$kind) {
    $r=PythonPatcher (TargetArgs $kind 'status') 30
    $body=$r.Stdout+"`n"+$r.Stderr
    $m=[regex]::Match($body,'(?i)status\s*:\s*(patched|unpatched|unknown)')
    if($r.ExitCode -eq 0 -and $m.Success) { return $m.Groups[1].Value.ToLowerInvariant() }
    return 'unknown'
}
function IsRunning([string]$kind) {
    if($kind -eq 'cli') { return [bool](Get-Process -Name 'agy' -ErrorAction SilentlyContinue) }
    return [bool]((Get-Process -Name 'Antigravity','language_server' -ErrorAction SilentlyContinue))
}
function PatchTarget([string]$kind) {
    $current=StatusOf $kind
    Say ('PATCH_'+$kind.ToUpperInvariant()+'_BEFORE') $current
    if($current -eq 'patched') { return $true }
    if($current -ne 'unpatched') { Say 'PATCH_SKIPPED' ('Unknown signature: '+$kind);return $false }
    if(IsRunning $kind) { Say 'PATCH_SKIPPED' ('Close running '+$kind+' application/daemon first');return $false }
    $file = if($kind -eq 'cli') {$script:CLI} else {$script:Manager}
    $backupDir=Join-Path $work 'pristine-backups'
    [void](New-Item -ItemType Directory -Path $backupDir -Force)
    $hash=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    $backup=Join-Path $backupDir ($kind+'-'+$hash+'.exe')
    if(-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $file -Destination $backup -ErrorAction Stop }
    if((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne $hash) { throw 'Pristine backup verification failed' }
    Say ('BACKUP_'+$kind.ToUpperInvariant()) $backup
    $r=PythonPatcher (TargetArgs $kind 'patch') 70
    if($r.ExitCode -ne 0) {
        Say 'PATCH_ERROR' ($kind+' exit='+$r.ExitCode)
        # QNIX has its own rollback. Confirm its result, and restore our
        # independently verified backup if it left a partially modified file.
        if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $hash) {
            if(-not (IsRunning $kind)) {
                Copy-Item -LiteralPath $backup -Destination $file -Force
                if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $hash) {
                    throw 'Emergency pristine restore failed; manual repair required'
                }
                Say 'PATCH_FAILURE_RESTORED' $kind
            } else { Say 'PATCH_FAILURE_RESTORE_BLOCKED' $kind }
        }
        return $false
    }
    $after=StatusOf $kind
    Say ('PATCH_'+$kind.ToUpperInvariant()+'_AFTER') $after
    if($after -eq 'patched') { $script:NewPatches+= $kind;return $true }
    if(-not (IsRunning $kind)) {
        Copy-Item -LiteralPath $backup -Destination $file -Force
        if((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq $hash) {
            Say 'UNVERIFIED_PATCH_RESTORED' $kind
        } else { throw 'Failed to restore pristine file after unverified patch' }
    }
    return $false
}
function RollbackNewPatches {
    foreach($target in $script:NewPatches) {
        try {
            if(IsRunning $target) { Say 'ROLLBACK_BLOCKED' ('Running '+$target);continue }
            $r=PythonPatcher (TargetArgs $target 'restore') 45
            $s=StatusOf $target
            Say ('ROLLBACK_'+$target.ToUpperInvariant()) ('exit='+$r.ExitCode+' status='+$s)
        } catch { Say 'ROLLBACK_ERROR' ($target+': '+$_.Exception.Message) }
    }
}

try {
    Say 'ANTIGRAVITY_CHECK' 'Windows / no credential collection / no VPN changes'
    if([Environment]::OSVersion.Platform -ne 'Win32NT') { Finish 'WINDOWS_ONLY' 'Requires Windows PowerShell' 2 }
    FindPrograms
    if($script:CLI) { Say 'FOUND_CLI' $script:CLI } else { Say 'FOUND_CLI' 'No' }
    if($script:Desktop) {
        Say 'FOUND_DESKTOP' $script:Desktop
        Say 'DESKTOP_VERSION' ([Diagnostics.FileVersionInfo]::GetVersionInfo($script:Desktop).ProductVersion)
    } else { Say 'FOUND_DESKTOP' 'No' }
    if($script:Manager) { Say 'FOUND_MANAGER' $script:Manager } else { Say 'FOUND_MANAGER' 'No' }
    if(-not $script:CLI -and -not $script:Desktop) { Finish 'NOT_INSTALLED' 'Install official Antigravity Desktop or CLI first' 2 }
    NetworkAudit
    $script:Before=LiveTest
    Say 'INITIAL_CLI_TEST' ($script:Before.State+' (exit '+$script:Before.ExitCode+')')
    $guiBefore=DesktopLiveTest -CloseIfStarted
    Say 'INITIAL_DESKTOP_TEST' ($guiBefore.State+'; '+$guiBefore.Reason)
    $cliReady=(-not $script:CLI -or $script:Before.State -eq 'PASS')
    $guiReady=(-not $script:Desktop -or $guiBefore.State -eq 'PASS')
    if($cliReady -and $guiReady) {
        if($script:CLI -and $script:Desktop) { Finish 'READY_ALL' 'CLI and Desktop returned separate verified live model responses; no patch needed' 0 }
        if($script:CLI) { Finish 'READY_CLI' 'Verified live CLI model response; Desktop not installed' 0 }
        Finish 'READY_DESKTOP' 'Verified live Desktop model response; CLI not installed' 0
    }
    # Server failures, login problems and inaccessible Electron controls are
    # not evidence that a client-side eligibility patch is appropriate.
    $failures=New-Object 'System.Collections.Generic.List[string]'
    if($script:CLI -and $script:Before.State -ne 'PASS') { $failures.Add('CLI='+$script:Before.State) }
    if($script:Desktop -and $guiBefore.State -ne 'PASS') { $failures.Add('Desktop='+$guiBefore.State) }
    $wantCLI=($script:CLI -and $script:Before.State -eq 'CLIENT_ELIGIBILITY')
    $wantManager=($script:Manager -and $guiBefore.State -eq 'CLIENT_ELIGIBILITY')
    if($DesktopEligibilityBlocked -and $script:Manager -and $guiBefore.State -ne 'PASS') {
        Say 'OPERATOR_ASSERTION' 'DesktopEligibilityBlocked was explicitly passed'
        $wantManager=$true
    }
    if(-not $wantCLI -and -not $wantManager) {
        if(@($failures | Where-Object {$_ -match 'REGION_DENIED'}).Count -gt 0) {
            Finish 'SERVER_REGION_DENIED' ($failures -join '; ') 4
        }
        if(@($failures | Where-Object {$_ -match 'AUTH_REQUIRED'}).Count -gt 0) {
            Finish 'LOGIN_REQUIRED' ($failures -join '; ') 3
        }
        Finish 'NOT_VERIFIED' (($failures -join '; ') + '. No evidence of a fixable local eligibility issue; no patch applied.') 5
    }
    if(-not $ApplyIfNeeded) { Finish 'PATCH_AVAILABLE' 'Compatible local eligibility issue detected. Re-run with -ApplyIfNeeded' 6 }
    if(-not (FindPython)) { Finish 'PYTHON_3_REQUIRED' 'Install Python 3 from python.org, rerun. No binaries changed.' 7 }
    FetchPinnedPatcher
    $ok=$true
    if($wantCLI) { if(-not (PatchTarget 'cli')) { $ok=$false } }
    if($wantManager) { if(-not (PatchTarget 'manager')) { $ok=$false } }
    if(-not $ok) { Finish 'PATCH_NOT_APPLICABLE' 'Unknown signature, running app or patch failure. Check logs and compatibility.' 8 }
    if($wantCLI) {
        $script:After=LiveTest
        Say 'FINAL_CLI_TEST' ($script:After.State+' (exit '+$script:After.ExitCode+')')
    } else { $script:After=$script:Before }
    if($wantManager) {
        $guiAfter=DesktopLiveTest
        Say 'FINAL_DESKTOP_TEST' ($guiAfter.State+'; '+$guiAfter.Reason)
    } else { $guiAfter=$guiBefore }
    $cliReady=(-not $script:CLI -or $script:After.State -eq 'PASS')
    $guiReady=(-not $script:Desktop -or $guiAfter.State -eq 'PASS')
    if($cliReady -and $guiReady) {
        if($script:CLI -and $script:Desktop) {Finish 'READY_ALL' 'Actual Gemini responses verified in CLI and Desktop' 0}
        if($script:CLI) {Finish 'READY_CLI' 'Actual Gemini response verified in CLI' 0}
        Finish 'READY_DESKTOP' 'Actual Gemini response verified in Desktop' 0
    }
    if($script:NewPatches.Count -gt 0) {
        Say 'POST_PATCH_NOT_VERIFIED' 'The model response was not proven in every installed application.'
        # A client eligibility patch can be functional despite UIA being unable
        # to read Electron output. Do not automatically remove it for UIA limits.
        if(($wantCLI -and $script:After.State -ne 'PASS') -or
           ($wantManager -and $guiAfter.State -in @('SERVER_REGION_DENIED','CLIENT_ELIGIBILITY'))) {
            Say 'ROLLBACK_REQUESTED' 'Post-patch model is still returning a definite error.'
            RollbackNewPatches
        }
    }
    Finish 'PARTIAL_OR_UNVERIFIED' ('CLI='+$script:After.State+'; Desktop='+$guiAfter.State+'; see Desktop UI and log') 9
} catch {
    Say 'UNEXPECTED_ERROR' $_.Exception.Message
    Finish 'ERROR' 'Review the error. No unsupported success claim.' 11
}