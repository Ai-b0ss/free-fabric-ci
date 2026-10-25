#Requires -Version 5.1
<#
  Interactive Desktop UI smoke test. Must be run inside the current Windows
  user's UNLOCKED INTERACTIVE desktop session. No direct token/cookie access.
  Uses ordinary Microsoft UI Automation and keyboard input in the app window.
  A PASS means the fully-composed nonce was observed in rendered accessibility
  text AFTER submission. If inaccessible, returns UNVERIFIED, never PASS.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ExePath,
    [ValidateRange(15,180)][int]$TimeoutSeconds=70,
    [switch]$CloseIfStarted
)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
if(-not ('AGDesktopWindow' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AGDesktopWindow {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd,int cmd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
'@
}
$script:Launched=$false
$script:WindowProc=$null
$script:Submitted=$false
$script:expected=$null
$script:visited=0
function Result([string]$state,[string]$reason) {
    [pscustomobject]@{State=$state;Reason=$reason;Submitted=$script:Submitted;Launched=$script:Launched}
}
function Get-VisibleWindow {
    $all=@(Get-Process -Name 'Antigravity' -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero -and -not $_.HasExited })
    if($all.Count -eq 0) { return $null }
    # Prefer the actual main process window, not an invisible helper.
    return ($all | Sort-Object -Property @{Expression={($_.MainWindowTitle -match 'Antigravity')};Descending=$true} | Select-Object -First 1)
}
function Snapshot([System.Windows.Automation.AutomationElement]$window) {
    $all=$window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition)
    $text=New-Object 'System.Collections.Generic.List[string]'
    $editCandidates=New-Object 'System.Collections.Generic.List[object]'
    # Limit DOM iteration so a very large chat does not stall the test.
    for($i=0;$i -lt [Math]::Min($all.Count,2500);$i++) {
      try {
        $el=$all.Item($i)
        $c=$el.Current
        $name=[string]$c.Name
        if(-not $c.IsOffscreen -and $name -and $name.Length -lt 2400) { $text.Add($name) }
        $ctype=$c.ControlType
        if($ctype -eq [System.Windows.Automation.ControlType]::Edit -or
           $ctype -eq [System.Windows.Automation.ControlType]::Document) {
            $pattern=$null
            if($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$pattern)) {
                $v=[string]$pattern.Current.Value
                if(-not $c.IsOffscreen -and $v.Length -le 2400 -and $v) { $text.Add($v) }
            }
            if($c.IsEnabled -and -not $c.IsOffscreen -and $c.IsKeyboardFocusable -and
               $c.BoundingRectangle.Width -gt 120 -and $c.BoundingRectangle.Height -gt 15) {
                $id=[string]$c.AutomationId
                $allNames=($name+' '+$id)
                $score=0
                if($ctype -eq [System.Windows.Automation.ControlType]::Edit) { $score+=2 }
                if($allNames -match '(?i)chat|message|prompt|assistant|gemini|type.*message|send|ask|agent|compose|сообщен|вопрос|запрос|напишите|отправить') { $score+=10 }
                if($allNames -match '(?i)search|find|filter|rename|title|file|path|terminal|code editor|address|поиск|найти|фильтр') { $score-=12 }
                if($c.BoundingRectangle.Width -ge 280) { $score++ }
                if($c.BoundingRectangle.Height -le 250) { $score++ }
                if($c.HasKeyboardFocus) { $score+=2 }
                $editCandidates.Add([pscustomobject]@{Element=$el;Score=$score;Name=$name;Id=$id})
            }
        }
      } catch { continue }
    }
    return [pscustomobject]@{Texts=$text;Candidates=$editCandidates}
}
function ReadComposerText($element) {
    try {
        $valuePattern=$null
        if($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$valuePattern)) {
            return [string]$valuePattern.Current.Value
        }
        $textPattern=$null
        if($element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$textPattern)) {
            return [string]$textPattern.DocumentRange.GetText(1000)
        }
    } catch {}
    return $null
}
function TrySendButton($root) {
    $buttons=@()
    try {
        $all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
        for($i=0;$i -lt [Math]::Min($all.Count,1500);$i++) {
            $el=$all.Item($i)
            $c=$el.Current
            if($c.ControlType -ne [System.Windows.Automation.ControlType]::Button -or
               -not $c.IsEnabled -or $c.IsOffscreen) {continue}
            if(([string]$c.Name) -match '(?i)^\s*(send|send message|submit message|отправить|отправить сообщение)\s*$') {
                $buttons+=,$el
            }
        }
        if($buttons.Count -ne 1) {return $false}
        $inv=$null
        if($buttons[0].TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$inv)) {
            $inv.Invoke()
            return $true
        }
    } catch {}
    return $false
}
function Classify($snapshot) {
    $s=($snapshot.Texts -join ' ')
    if($s -match '(?i)user location is not supported|unsupported region|unsupported location|HTTP.?400.{0,100}(location|region)|недоступно в вашем регионе') {return 'SERVER_REGION_DENIED'}
    if($s -match '(?i)eligibility check|not eligible|ineligible|eligibility restriction|проверка доступности') {return 'CLIENT_ELIGIBILITY'}
    if($s -match '(?i)sign in with google|sign in to|please sign in|log in to|войти через google|войдите в аккаунт') {return 'AUTH_REQUIRED'}
    return 'NO_CHAT_INPUT'
}
try {
    if(-not [Environment]::UserInteractive) { Result 'UNVERIFIED' 'Requires an interactive, unlocked Windows desktop';return }
    if(-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) { Result 'NO_DESKTOP' 'EXE not found';return }
    $win=Get-VisibleWindow
    if(-not $win) {
        # Launch a window only from the user's own interactive session.
        Start-Process -FilePath $ExePath -ErrorAction Stop | Out-Null
        $script:Launched=$true
        $deadline=(Get-Date).AddSeconds(40)
        do { Start-Sleep -Milliseconds 800; $win=Get-VisibleWindow } until ($win -or (Get-Date) -ge $deadline)
    }
    if(-not $win) { Result 'UNVERIFIED' 'Desktop did not expose a main window';return }
    $script:WindowProc=$win
    [void][AGDesktopWindow]::ShowWindow($win.MainWindowHandle,9)
    [void][AGDesktopWindow]::SetForegroundWindow($win.MainWindowHandle)
    Start-Sleep -Milliseconds 1100
    $root=[System.Windows.Automation.AutomationElement]::FromHandle($win.MainWindowHandle)
    $snapshot=Snapshot $root
    # Many installs open on a landing page with a 'New chat' button instead
    # of a composer. Invoking that UIA button is safe; no chat is deleted.
    if(@($snapshot.Candidates | Where-Object {$_.Score -ge 9}).Count -eq 0) {
        $all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
        $startButtons=@()
        for($i=0;$i -lt [Math]::Min($all.Count,1500);$i++) {
            try {
                $el=$all.Item($i)
                if($el.Current.ControlType -ne [System.Windows.Automation.ControlType]::Button -or
                   -not $el.Current.IsEnabled -or $el.Current.IsOffscreen) {continue}
                if($el.Current.Name -match '(?i)^\s*(new chat|new conversation|start chatting|новый чат|новая беседа)\s*$') {
                    $startButtons+= $el
                }
            } catch {}
        }
        if($startButtons.Count -eq 1) {
            $inv=$null
            if($startButtons[0].TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$inv)) {
                $inv.Invoke()
                Start-Sleep -Seconds 1
                $snapshot=Snapshot $root
            }
        }
    }
    # Avoid selecting a search bar, code editor, or arbitrary text field.
    $candidates=@($snapshot.Candidates | Sort-Object -Property Score -Descending)
    if($candidates.Count -eq 0 -or $candidates[0].Score -lt 9) {
        $priorState=Classify $snapshot
        # An old error message can still be visible in a previous chat. Do not
        # patch based solely on a stale, unsent message in that situation.
        if($priorState -eq 'CLIENT_ELIGIBILITY') { $priorState='UNVERIFIED' }
        Result $priorState 'No confidently identified chat composer; did not type into an unknown field'
        return
    }
    if($candidates.Count -gt 1 -and $candidates[1].Score -ge $candidates[0].Score) {
        Result 'UNVERIFIED' 'Multiple equally plausible chat composers; did not type into an unknown field'
        return
    }
    $target=$candidates[0].Element
    $nonce=[guid]::NewGuid().ToString('N').Substring(0,8).ToUpperInvariant()
    # The *joined* marker is absent from the input prompt: detecting it in the
    # accessibility tree after Enter thus cannot be a mere echo of the prompt.
    $script:expected='AGUI_LIVE_'+$nonce
    $prompt='Reply with only these three pieces joined by underscores: AGUI LIVE '+$nonce
    [void][AGDesktopWindow]::SetForegroundWindow($win.MainWindowHandle)
    $target.SetFocus()
    Start-Sleep -Milliseconds 300
    if([AGDesktopWindow]::GetForegroundWindow() -ne $win.MainWindowHandle) {
        Result 'UNVERIFIED' 'Antigravity is not foreground; refused to type into another app'
        return
    }
    # SendKeys operates on the focused UI element. Only plain ASCII appears in
    # prompt, avoiding SendKeys metacharacters such as { } + ^ % ~ ( ).
    $baselineState=Classify $snapshot
    [System.Windows.Forms.SendKeys]::SendWait($prompt)
    Start-Sleep -Milliseconds 300
    $composerValue=ReadComposerText $target
    if(-not [string]::IsNullOrWhiteSpace($composerValue) -and $composerValue -notlike ('*'+$prompt+'*')) {
        Result 'UNVERIFIED' 'Chat composer did not contain the intended prompt; nothing submitted'
        return
    }
    if(-not (TrySendButton $root)) {
        # Some builds have no accessible Send button; use Enter in composer.
        [void][AGDesktopWindow]::SetForegroundWindow($win.MainWindowHandle)
        $target.SetFocus()
        if([AGDesktopWindow]::GetForegroundWindow() -ne $win.MainWindowHandle) {
            Result 'UNVERIFIED' 'Focus changed before submitting; prompt not sent'
            return
        }
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
    }
    $script:Submitted=$true
    $ends=(Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Seconds 2
        try {
            $after=Snapshot $root
            foreach($part in $after.Texts) {
                if($part.Contains($script:expected)) {
                    Result 'PASS' ('Desktop replied with verified marker '+$script:expected)
                    return
                }
            }
            $state=Classify $after
            if(($state -eq 'SERVER_REGION_DENIED' -or $state -eq 'CLIENT_ELIGIBILITY' -or $state -eq 'AUTH_REQUIRED') -and
               $state -ne $baselineState) {
                Result $state 'A newly observed error is visible in Desktop accessibility tree'
                return
            }
        } catch { }
    } until ((Get-Date) -ge $ends)
    Result 'UNVERIFIED' 'Request was submitted but model response was not observable through Windows UI Automation; inspect Desktop window'
} catch {
    Result 'UNVERIFIED' ('Desktop automation error: '+$_.Exception.Message)
} finally {
    if($CloseIfStarted -and $script:Launched -and $script:WindowProc) {
        try { [void]$script:WindowProc.CloseMainWindow() } catch { }
    }
}