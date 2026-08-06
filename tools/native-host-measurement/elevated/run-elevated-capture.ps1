# Elevated ProcMon capture for the chrome-native-host acceptance protocol.
# See RUNBOOK.md beside this file, and the protocol in docs/internals.md.
# Runs the capture UNFILTERED to a backing file (analyzer scopes to the store roots);
# a capture-time path filter would require an undocumented binary .pmc and risks
# excluding its own positive control.

[CmdletBinding()]
param(
    [int]$HelperOnlyMinutes = 3,
    [switch]$KeepPml,
    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'

# --- self-elevate (fresh conhost, outside any Claude process tree) ---------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Relaunching elevated (one UAC prompt)..."
    $exe = (Get-Process -Id $PID).Path
    Start-Process -Verb RunAs -FilePath $exe -ArgumentList @(
        '-NoExit', '-File', $PSCommandPath,
        '-HelperOnlyMinutes', $HelperOnlyMinutes, $(if ($KeepPml) { '-KeepPml' }), '-Elevated'
    )
    return
}

# --- shared state -----------------------------------------------------------------
$stamp   = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
$outDir  = Join-Path $PSScriptRoot "capture-$stamp"
New-Item -ItemType Directory -Path $outDir | Out-Null
$pml     = Join-Path $outDir 'capture.pml'
$csv     = Join-Path $outDir 'capture.csv'
$timelinePath = Join-Path $outDir 'workload-timeline.json'
$timeline = [System.Collections.Generic.List[object]]::new()

function Mark([string]$Phase, [string]$Event, [string]$Detail = '') {
    $rec = [ordered]@{
        utc = (Get-Date).ToUniversalTime().ToString('o'); phase = $Phase
        event = $Event; detail = $Detail
    }
    $timeline.Add([pscustomobject]$rec)
    # flush every mark so a crash still leaves a usable timeline
    $timeline | ConvertTo-Json -Depth 4 | Set-Content -Path $timelinePath -Encoding utf8
    Write-Host ("  [{0}] {1}: {2} {3}" -f $rec.utc, $Phase, $Event, $Detail)
}

function Ask([string]$Prompt) { Read-Host ("`n>> " + $Prompt) }

# --- resolve store roots ----------------------------------------------------------
$roots = @()
$msix = Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue |
    ForEach-Object { Join-Path $_.FullName 'LocalCache\Roaming\Claude\claude-code-sessions' } |
    Where-Object { Test-Path $_ }
$roots += $msix
$classic = Join-Path $env:APPDATA 'Claude\claude-code-sessions'
if (Test-Path $classic) { $roots += $classic }
if (-not $roots) { throw "No store root found - nothing to measure." }
Write-Host "Store roots under capture:"; $roots | ForEach-Object { Write-Host "  $_" }
Mark 'P0' 'store-roots' ($roots -join ' | ')

# --- process probes ---------------------------------------------------------------
function Get-DesktopAppProcs {
    # The MSIX app family: any process whose image sits under \Packages\Claude_...,
    # EXCEPT the helper itself (it survives the app by design and is probed separately).
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path -match '\\Packages\\Claude_' -and
        $_.Path -notmatch '\\ChromeNativeHost\\chrome-native-host\.exe$'
    }
}
function Get-HelperProcs {
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path -match '\\Packages\\Claude_.*\\ChromeNativeHost\\chrome-native-host\.exe$'
    }
}
function Wait-Until([scriptblock]$Cond, [string]$What, [int]$TimeoutSec = 600) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while (-not (& $Cond)) {
        if ($sw.Elapsed.TotalSeconds -gt $TimeoutSec) { throw "Timed out waiting for: $What" }
        Start-Sleep -Seconds 2
    }
}

# --- record the binding identifiers (helper build, package, Chrome, OS) -----------
$helper = Get-HelperProcs | Select-Object -First 1
if (-not $helper) {
    Write-Host "Helper not running. Open Chrome and use the Claude extension once, then continue."
    Ask "Press Enter when Chrome + extension are active"
    Wait-Until { Get-HelperProcs } 'chrome-native-host.exe to appear' 120
    $helper = Get-HelperProcs | Select-Object -First 1
}
$helperPath = $helper.Path
$binding = [ordered]@{
    helper_path    = $helperPath
    helper_sha256  = (Get-FileHash -Algorithm SHA256 $helperPath).Hash
    helper_version = (Get-Item $helperPath).VersionInfo.FileVersion
    helper_bytes   = (Get-Item $helperPath).Length
    package        = (Split-Path -Leaf ($helperPath -replace '\\LocalCache\\.*$', ''))
    chrome_version = (Get-Process chrome -ErrorAction SilentlyContinue |
        Where-Object Path | Select-Object -First 1 |
        ForEach-Object { $_.Path } | ForEach-Object { (Get-Item $_).VersionInfo.ProductVersion })
    windows        = [Environment]::OSVersion.VersionString
    captured_utc   = (Get-Date).ToUniversalTime().ToString('o')
}
$binding | ConvertTo-Json | Set-Content (Join-Path $outDir 'binding.json') -Encoding utf8
Mark 'P0' 'binding' "helper $($binding.helper_version) sha256=$($binding.helper_sha256.Substring(0,12))..."

# --- locate procmon ---------------------------------------------------------------
$procmon = Get-Command procmon64, procmon -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $procmon) {
    $wingetLinks = "$env:LOCALAPPDATA\Microsoft\WinGet\Links"
    $cand = Get-ChildItem $wingetLinks -Filter 'procmon*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { $procmon = $cand.FullName }
}
if (-not $procmon) { throw "Process Monitor not found. Install it first: winget install Microsoft.Sysinternals.ProcessMonitor" }
Mark 'P0' 'procmon' $procmon

# --- canary heartbeat (loss probe + store-root positive control) ------------------
$canaryStop = Join-Path $outDir 'canary.stop'
$canaryJob = Start-Job -ArgumentList ($roots -join ';'), $canaryStop, $timelinePath -ScriptBlock {
    param($rootsJoined, $stopFile, $tlPath)
    $roots = $rootsJoined -split ';'
    $n = 0
    while (-not (Test-Path $stopFile)) {
        $n++
        foreach ($root in $roots) {
            try {
                $tmp = Join-Path $root ".ccs-capture-canary-$n.tmp"
                $fin = Join-Path $root ".ccs-capture-canary-$n.txt"
                Set-Content -Path $tmp -Value "canary $n $((Get-Date).ToUniversalTime().ToString('o'))"
                Move-Item -Path $tmp -Destination $fin -Force
                Remove-Item -Path $fin -Force
                Add-Content -Path ($tlPath + '.canary.log') -Value ("{0} canary {1} {2}" -f `
                    (Get-Date).ToUniversalTime().ToString('o'), $n, $root)
            } catch {
                Add-Content -Path ($tlPath + '.canary.log') -Value ("{0} canary-ERROR {1} {2}" -f `
                    (Get-Date).ToUniversalTime().ToString('o'), $n, $_.Exception.Message)
            }
        }
        Start-Sleep -Seconds 30
    }
}

# --- start capture ----------------------------------------------------------------
Write-Host "`nStarting ProcMon (unfiltered, backing file)..."
& $procmon /AcceptEula /Quiet /Minimized /BackingFile $pml
Start-Sleep -Seconds 5   # let the driver settle before the workload begins
Mark 'P0' 'capture-start' $pml

try {
    # --- P1: controls, app open ---------------------------------------------------
    Write-Host "`n=== PHASE 1 - controls (desktop app OPEN) ==="
    Mark 'P1' 'begin'
    if (-not (Get-DesktopAppProcs)) {
        Ask "Desktop app not detected - open it, then press Enter"
        Wait-Until { Get-DesktopAppProcs } 'desktop app to appear' 120
    }
    Mark 'P1' 'app-verified-running' ((Get-DesktopAppProcs | Measure-Object).Count.ToString() + ' procs')
    $d = Ask "Use the DESKTOP APP briefly (open a chat, send a throwaway message) so its own store writes land in the trace. Describe what you did"
    Mark 'P1' 'app-store-activity' $d
    $d = Ask "Now use the CHROME EXTENSION several times (count them). What did you do, and how many round trips?"
    Mark 'P1' 'extension-roundtrips-app-open' $d

    # --- P2: the decisive window ----------------------------------------------------
    Write-Host "`n=== PHASE 2 - close the desktop app; helper must SURVIVE ==="
    Mark 'P2' 'begin'
    Ask "Fully close the Claude desktop app now (system tray too). Press Enter when done"
    Wait-Until { -not (Get-DesktopAppProcs) } 'desktop app processes to exit' 300
    if (-not (Get-HelperProcs)) {
        Mark 'P2' 'HELPER-DIED-WITH-APP' 'decisive window unavailable this run'
        Write-Host "NOTE: helper exited with the app - the helper-only window did not occur. Continuing (the run still yields controls)."
    } else {
        Mark 'P2' 'app-closed-helper-alive' ("helper pid " + ((Get-HelperProcs | Select-Object -First 1).Id))
        Write-Host ("Holding the helper-only window for {0} minute(s). Use the extension AGAIN during this window." -f $HelperOnlyMinutes)
        $until = (Get-Date).AddMinutes($HelperOnlyMinutes)
        while ((Get-Date) -lt $until) {
            Start-Sleep -Seconds 15
            if (-not (Get-HelperProcs)) { Mark 'P2' 'helper-exited-early'; break }
            if (Get-DesktopAppProcs)   { Mark 'P2' 'APP-RELAUNCHED-DURING-WINDOW' 'window contaminated'; break }
        }
        $d = Ask "Window held. What extension actions did you perform during it, and how many?"
        Mark 'P2' 'extension-roundtrips-app-closed' $d
    }

    # --- P3: Chrome exit ------------------------------------------------------------
    Write-Host "`n=== PHASE 3 - exit Chrome fully; helper must EXIT ==="
    Mark 'P3' 'begin'
    Ask "Fully exit Chrome now (all windows; check the tray). Press Enter when done"
    Wait-Until { -not (Get-HelperProcs) } 'helper to exit' 300
    Mark 'P3' 'helper-exited'
}
finally {
    # --- stop capture, export, clean up --------------------------------------------
    Write-Host "`nStopping capture..."
    Set-Content -Path $canaryStop -Value 'stop'
    Receive-Job $canaryJob -Wait -ErrorAction SilentlyContinue | Out-Null; Remove-Job $canaryJob -Force -ErrorAction SilentlyContinue
    & $procmon /Terminate | Out-Null
    Start-Sleep -Seconds 5
    Mark 'P4' 'capture-stopped'
    Write-Host "Exporting CSV (this can take a while on a large trace)..."
    & $procmon /AcceptEula /Quiet /OpenLog $pml /SaveAs $csv
    Mark 'P4' 'csv-exported' $csv
    if (-not $KeepPml -and (Test-Path $csv) -and ((Get-Item $csv).Length -gt 0)) {
        Remove-Item $pml -Force; Mark 'P4' 'pml-deleted' 'pass -KeepPml to retain'
    }
}

Write-Host "`nDone. Output directory:`n  $outDir"
Write-Host "Next: have a Claude session run  python tools/native-host-measurement/elevated/analyze_capture.py `"$outDir`""
