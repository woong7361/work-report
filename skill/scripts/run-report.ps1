# Scheduled runner: asks the agent to produce the report for a date range,
# notifies the desktop, fills in days that were missed, and keeps the folder tidy.
# ASCII only on purpose (see _env.ps1). Report text comes from SKILL.md,
# notification text from messages.json.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File run-report.ps1 -Mode daily
#   powershell -NoProfile -ExecutionPolicy Bypass -File run-report.ps1 -Mode weekly -DryRun
#   powershell -NoProfile -ExecutionPolicy Bypass -File run-report.ps1 -Agent codex

param(
    [ValidateSet('daily', 'weekly')][string]$Mode = 'daily',
    [ValidateSet('claude', 'codex')][string]$Agent,
    [string]$From,
    [string]$To,
    [string]$Root,
    [int]$Backfill = -1,        # -1 = use config; 0 = never look back
    [switch]$NoToast,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$root = Get-ReportRoot -Override $Root
$cfg = Get-ReportConfig -Root $root
if (-not $Agent) { $Agent = if ($cfg -and $cfg.agent) { [string]$cfg.agent } else { 'claude' } }
if ($NoToast -or ($cfg -and $cfg.notify -eq $false)) { $env:WORK_REPORT_NO_TOAST = '1' }
if ($Backfill -lt 0) { $Backfill = if ($cfg -and $null -ne $cfg.backfill_days) { [int]$cfg.backfill_days } else { 2 } }

$stamp = Get-Date -Format 'yyyy-MM-dd'
$logDir = Join-Path (Join-Path $root 'runlog') $stamp.Substring(0, 7)
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir "$stamp-$Mode.log"
function Log([string]$line) { "[{0}] $line" -f (Get-Date -Format 'HH:mm:ss') | Add-Content -Path $log -Encoding utf8 }

$msg = Get-Messages

function Get-Prompts {
    $path = Join-Path (Join-Path (Get-SkillRoot) 'prompts') 'ask.json'
    try { return Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return $null }
}

$prompts = Get-Prompts

function Get-WeeklyRange {
    # The reporting week ends on a fixed weekday and spans a fixed number of days.
    # Default: Thursday through the following Wednesday (five working days).
    param([datetime]$Ref, $Config)
    $endDay = 'Wednesday'
    $span = 7
    if ($Config -and $Config.weekly) {
        if ($Config.weekly.end_day) { $endDay = [string]$Config.weekly.end_day }
        if ($Config.weekly.span_days) { $span = [int]$Config.weekly.span_days }
    }
    $target = [int][System.DayOfWeek]$endDay
    $back = ([int]$Ref.DayOfWeek - $target + 7) % 7
    $end = $Ref.Date.AddDays(-$back)
    return @{ From = $end.AddDays(-($span - 1)).ToString('yyyy-MM-dd'); To = $end.ToString('yyyy-MM-dd') }
}

function Get-ReportPath {
    param([string]$ModeName, [string]$RangeFrom, [string]$RangeTo)
    $name = if ($RangeFrom -eq $RangeTo) { $RangeFrom } else { "$RangeFrom`_$RangeTo" }
    return (Join-Path $root "$ModeName\$($RangeTo.Substring(0,7))\$name.md")
}

function Notify {
    param([string]$State, [string]$Detail, [string]$RangeText)
    if (-not $msg) { return }
    $body = ([string]$msg.body.$State).
    Replace('{range}', $RangeText).
    Replace('{name}', $(if ($Detail) { Split-Path -Leaf $Detail } else { '' })).
    Replace('{folder}', $(if ($Detail) { Split-Path -Parent $Detail } else { '' })).
    Replace('{path}', $Detail).Replace('{log}', $Detail)
    if ($State -ne 'start') { Hide-Toast }     # replace "in progress"
    $toast = @{
        Title   = $msg.$Mode.$State
        Message = $body
        Kind    = $(if ($State -eq 'fail') { 'error' } else { 'info' })
    }
    # Every click goes to the viewer - the report when it worked, the run log
    # when it did not. A folder button would send the reader to Explorer, which
    # is the one place that cannot show either.
    if ($State -ne 'start' -and $Detail) {
        $toast.OpenPath = $Detail
    }
    Show-Toast @toast | Out-Null
}

function Invoke-Report {
    param([string]$RangeFrom, [string]$RangeTo)

    $expected = Get-ReportPath -ModeName $Mode -RangeFrom $RangeFrom -RangeTo $RangeTo
    $rangeText = if ($RangeFrom -eq $RangeTo) { $RangeFrom } else { "$RangeFrom ~ $RangeTo" }

    # The agent is told what to do, not how; SKILL.md holds the procedure.
    $ask = [string]$prompts.modes.$Mode
    $ask = $ask.Replace('{from}', $RangeFrom).Replace('{to}', $RangeTo)
    # Hand over the exact files to follow. Leaving the agent to find them means
    # a silent miss when it decides not to look.
    $fmt = Get-FormatFile -Name 'report-format.md' -Root $root -Config $cfg -Flag 'custom_format'
    $rules = Get-FormatFile -Name 'writing-rules.md' -Root $root -Config $cfg -Flag 'custom_rules'
    $samples = Get-SampleFile -Root $root -Config $cfg
    if ($fmt) { $ask += ' ' + ([string]$prompts.files.report_format).Replace('{path}', $fmt) }
    if ($rules) { $ask += ' ' + ([string]$prompts.files.writing_rules).Replace('{path}', $rules) }
    if ($samples) { $ask += ' ' + ([string]$prompts.files.samples).Replace('{path}', $samples) }
    # Counted facts about how the person has filled the submission form before.
    # Written by pms.ps1 -Fetch; absent until that has run, and absent for anyone
    # who does not use the form. Passed only when it exists, like the files above.
    # The issue list changes between runs, so it is refreshed here rather than
    # read from whatever was left over. No token means no list, and the report
    # is written without issue links - which is what people who do not use them
    # get anyway. A failure here must not stop the report.
    # The token is kept in the vault, never in config.json, so the vault file is
    # what says whether a list can be fetched at all.
    $issues = Join-Path $root 'pms\open-issues.md'
    if (Test-Path (Join-Path $root 'pms\secrets.dat')) {
        try { & (Join-Path $PSScriptRoot 'pms.ps1') -Issues -Root $root | Out-Null }
        catch { Log "  issues: $($_.Exception.Message)" }
    }
    if (Test-Path $issues) { $ask += ' ' + ([string]$prompts.files.issues).Replace('{path}', $issues) } else { $issues = $null }

    $patterns = Join-Path $root 'pms\patterns.md'
    if (Test-Path $patterns) { $ask += ' ' + ([string]$prompts.files.patterns).Replace('{path}', $patterns) }
    else { $patterns = $null }
    if ($fmt -or $rules -or $samples -or $patterns -or $issues) { $ask += [string]$prompts.read_first }

    # The skill may be installed under another name to avoid a clash, so take
    # the name from the folder this script sits in rather than assuming it.
    $skillName = Split-Path -Leaf (Get-SkillRoot)

    # config.json decides which CLI runs, so it can name one the skill was never
    # installed for. That agent would start, find no skill and write nothing.
    #
    # The homes come from the same function the installer used, not from the
    # config key alone: the key is absent for an agent that was not installed,
    # and reading it directly turned that into a parameter binding error before
    # anything could be logged - the one case this check exists to report.
    $agentHomes = @(Get-AgentHomes -Agent $Agent -Config $cfg)
    # Keep the homes that actually carry the skill, not just the fact that one
    # of them does: the agent is pointed at a single home further down, and
    # taking the first of all homes could hand it one where the skill is absent.
    $withSkill = @($agentHomes | Where-Object { Test-Path (Join-Path $_ "skills\$skillName") })
    if ($agentHomes.Count -gt 0 -and $withSkill.Count -eq 0) {
        Log "skill '$skillName' is not installed for $Agent"
        Notify -State 'fail' -Detail $log -RangeText $rangeText
        # Continue, not the script-wide Stop preference: a terminating Write-Error
        # would abandon the remaining backfill days and skip Remove-OldFiles.
        Write-Error "The skill is not installed for $Agent. Run: install.ps1 -Agents $Agent" -ErrorAction Continue
        return $false
    }

    if ($Agent -eq 'claude') {
        $bin = Resolve-Claude -Preferred $cfg.claude_bin
        $argv = @('-p', "/$skillName $ask", '--dangerously-skip-permissions', '--add-dir', $root)
    }
    else {
        $bin = Resolve-Codex -Preferred $cfg.codex_bin
        $argv = @('exec', "Use the $skillName skill: $ask", '--dangerously-bypass-approvals-and-sandbox',
            '--skip-git-repo-check', '-C', $root)
    }

    Log "mode=$Mode agent=$Agent range=$RangeFrom..$RangeTo bin=$($bin.Version)"

    if ($DryRun) {
        # Write-Host, not Write-Output: this function's output stream is its
        # return value, so anything written there is swallowed by the caller.
        Write-Host "DRYRUN mode=$Mode agent=$Agent range=$RangeFrom..$RangeTo"
        Write-Host "  $($bin.Path) $($argv -join ' ')"
        Write-Host "  expected output: $expected"
        Notify -State 'start' -Detail $expected -RangeText $rangeText
        Log '  dryrun'
        return $true
    }

    $env:WORK_REPORT_DIR = $root
    # Point the agent at the home the installer recorded. A scheduled task starts
    # with a bare environment, so without this the agent would fall back to the
    # default home and miss the skill, the settings and the sign-in kept elsewhere.
    $agentHome = @($withSkill)[0]
    if ($agentHome) {
        if ($Agent -eq 'claude') { $env:CLAUDE_CONFIG_DIR = $agentHome } else { $env:CODEX_HOME = $agentHome }
    }

    $before = if (Test-Path $expected) { (Get-Item $expected).LastWriteTimeUtc } else { [datetime]::MinValue }
    Notify -State 'start' -Detail $expected -RangeText $rangeText

    # Windows PowerShell turns a native program's stderr into a terminating error
    # while the preference is Stop, so a single warning line from the agent would
    # kill this script before it could report the outcome.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Run from the report folder. Otherwise the agent starts in whatever directory
    # launched it - C:\Windows\System32 under Task Scheduler, or someone else's
    # project, whose own instruction files would then steer how the report is written.
    Push-Location $root
    try {
        & $bin.Path @argv 2>&1 | Add-Content -Path $log -Encoding utf8
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
        $ErrorActionPreference = $prevEAP
    }

    # Exit code alone is not proof: the agent can finish cleanly without writing
    # the report. The run counts as done only if the file is there and is new.
    $written = (Test-Path $expected) -and ((Get-Item $expected).LastWriteTimeUtc -gt $before)
    Log "exit=$code written=$written -> $expected"

    if ($code -eq 0 -and $written) {
        # The agent is told to check the submission section itself. This is the
        # second pair of eyes: a fence or an indent that slipped through would
        # otherwise be found only after it had been pasted into the form.
        # pms.ps1, not pms.py: the interpreter name differs per machine and the
        # wrapper is the one place that knows how to find it.
        try {
            $check = & (Join-Path $PSScriptRoot 'pms.ps1') -Validate $expected -Root $root 2>&1
            if ($LASTEXITCODE -ne 0) { foreach ($line in $check) { Log "  submission: $line" } }
        }
        catch { Log "  submission: check failed - $($_.Exception.Message)" }
        Notify -State 'done' -Detail $expected -RangeText $rangeText
        return $true
    }
    Write-ErrorLog -Root $root -Where 'runner' `
        -Message "$Mode $RangeFrom..$RangeTo failed (exit=$code written=$written agent=$Agent). see $log"
    Notify -State 'fail' -Detail $log -RangeText $rangeText
    Write-Error "work-report $Mode failed (exit=$code, written=$written). See $log" -ErrorAction Continue
    return $false
}

function Remove-OldFiles {
    # Reports are kept forever; only the collected records and run logs age out.
    $months = if ($cfg -and $cfg.retain_months) { [int]$cfg.retain_months } else { 0 }
    if ($months -le 0) { return }
    $cutoff = (Get-Date).AddMonths(-$months).ToString('yyyy-MM')
    foreach ($area in 'raw', 'runlog') {
        $base = Join-Path $root $area
        if (-not (Test-Path $base)) { continue }
        Get-ChildItem $base -Directory | Where-Object { $_.Name -lt $cutoff } | ForEach-Object {
            Remove-Item $_.FullName -Recurse -Force
            Log "pruned $area\$($_.Name)"
        }
    }
}

# ---------------------------------------------------------------- what to run
$explicitRange = [bool]$From
if (-not $From) {
    if ($Mode -eq 'weekly') {
        $r = Get-WeeklyRange -Ref (Get-Date) -Config $cfg
        $From = $r.From; $To = $r.To
    }
    else {
        $From = Get-Date -Format 'yyyy-MM-dd'
    }
}
if (-not $To) { $To = $From }

$ranges = @()
if ($Mode -eq 'daily' -and -not $explicitRange -and $Backfill -gt 0) {
    # A machine that was off at the scheduled time never reports that day, and
    # the next run would only cover the new day. Fill the gap on the way past.
    for ($i = $Backfill; $i -ge 1; $i--) {
        $d = (Get-Date).Date.AddDays(-$i)
        if ($d.DayOfWeek -eq 'Saturday' -or $d.DayOfWeek -eq 'Sunday') { continue }
        $ds = $d.ToString('yyyy-MM-dd')
        if (Test-Path (Get-ReportPath -ModeName 'daily' -RangeFrom $ds -RangeTo $ds)) { continue }
        $ranges += @{ From = $ds; To = $ds }
    }
}
$ranges += @{ From = $From; To = $To }

# ---------------------------------------------------------------- run
# One report at a time: a second run would write the same file underneath the first.
$lock = Join-Path $root 'runlog\.running'
if (-not $DryRun) {
    if (Test-Path $lock) {
        $age = [datetime]::UtcNow - (Get-Item $lock).LastWriteTimeUtc
        if ($age.TotalMinutes -lt 45) {
            Log "another run is in progress (started $([int]$age.TotalMinutes) min ago); skipping"
            Write-Output 'work-report: another run is in progress; skipping.'
            # Exit 2, not 0: nothing was written, but nothing failed either. The
            # viewer shows what it is told, and a plain 0 with no new report
            # reads as a failure on a run that was merely stood down.
            exit 2
        }
        Log 'stale lock removed'
        Remove-Item $lock -Force -ErrorAction SilentlyContinue
    }
    Set-Content -Path $lock -Value "$PID $Mode $(Get-Date -Format s)" -Encoding utf8
}

try {
    $ok = $true
    foreach ($r in $ranges) {
        if (-not (Invoke-Report -RangeFrom $r.From -RangeTo $r.To)) { $ok = $false }
    }
    if ($ok) { Remove-OldFiles }
}
finally {
    if (-not $DryRun) { Remove-Item $lock -Force -ErrorAction SilentlyContinue }
}
