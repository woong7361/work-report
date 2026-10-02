# work-report uninstaller (Windows). ASCII only on purpose.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1 -PurgeReports

param(
    [string]$Root,
    [string]$SkillName = 'work-report',
    [switch]$PurgeReports,  # also delete the generated reports (not reversible)
    [switch]$Yes            # skip the confirmation (for unattended use)
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'skill\scripts\_env.ps1')

# Removing is done in pieces, and a piece that fails must not take the rest
# with it: the scheduled tasks go first, so an abort halfway would leave an
# install that no longer runs but is still on disk, with only a stack trace
# to explain it. Each step reports and carries on, and the failures are
# listed at the end where they can be acted on.
$failed = @()
function Try-Step {
    param([string]$What, [scriptblock]$Do)
    try { & $Do }
    catch {
        $script:failed += "$What : $($_.Exception.Message)"
        Write-Host "could not remove $What"
    }
}

$m = Get-Messages
# Double-clicking the .cmd lands here, so ask before undoing an install.
if (-not $Yes) {
    $ask = if ($m -and $m.install -and $m.install.confirm) { $m.install.confirm } else { 'Remove work-report? [y/N]' }
    if ($PurgeReports -and $m -and $m.install -and $m.install.confirm_purge) { $ask = $m.install.confirm_purge }
    # Read-Host throws when there is no console to answer from. Removing things
    # is not undoable, so anything other than a typed yes must cancel.
    # Quoted on purpose: $null -notmatch is false, not true, so an unanswered
    # prompt would fall through to removing things.
    $answer = $null
    try { $answer = Read-Host $ask } catch { }
    if ("$answer".Trim() -notmatch '^[Yy]') {
        Write-Host ($(if ($m -and $m.install -and $m.install.cancelled) { $m.install.cancelled } else { 'Cancelled.' }))
        return
    }
}

# The scheduled tasks, the click handler, the notification identity and the
# Start Menu entry are named after the tool, not after -SkillName: one machine
# runs one work-report, and -SkillName only moves the skill folder aside when
# another skill already owns that name.
foreach ($name in 'work-report-daily', 'work-report-weekly') {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Try-Step "scheduled task $name" {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "removed scheduled task: $name"
        }
    }
}

foreach ($k in 'HKCU:\Software\Classes\workreport',
    'HKCU:\Software\Classes\AppUserModelId\WorkReport.Notify') {
    if (Test-Path $k) {
        Try-Step "registry key $k" { Remove-Item $k -Recurse -Force; Write-Host "removed registry key: $k" }
    }
}
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\work-report.lnk'
if (Test-Path $lnk) {
    Try-Step 'Start Menu entry' { Remove-Item $lnk -Force; Write-Host 'removed Start Menu entry' }
}

# An agent can have several homes and the install puts the skill in all of them,
# so removing only the one this shell points at would leave copies behind.
# config.json lists what was installed; the homes are checked as well in case it
# was edited or the skill was installed by an older version.
$root = Get-ReportRoot -Override $Root
$cfg = Get-ReportConfig -Root $root
# A running viewer would go on serving a tool that is being removed, and it
# holds the files this is about to delete.
Try-Step 'running app' { if (Stop-Viewer -Root $root) { Write-Host 'closed the running app' } }
$targets = @()
if ($cfg -and $cfg.skill_dirs) { $targets += @($cfg.skill_dirs) }
foreach ($a in 'claude', 'codex') {
    foreach ($h in (Get-AgentHomes -Agent $a -Config $cfg)) {
        $targets += (Join-Path $h "skills\$SkillName")
    }
}
foreach ($target in ($targets | Where-Object { $_ } | Select-Object -Unique)) {
    if (-not (Test-Path $target)) { continue }
    $item = Get-Item $target -Force
    # Only remove what this installer put there; a same-named skill from
    # somewhere else must survive an uninstall.
    if (-not ($item.LinkType -or (Test-Path (Join-Path $target 'scripts\collect.py')))) {
        Write-Host "kept (not installed by work-report): $target"
        continue
    }
    Try-Step "skill $target" {
        if ($item.LinkType) { $item.Delete() } else { Remove-Item $target -Recurse -Force }
        Write-Host "removed skill: $target"
    }
}

# The staged copy that -Copy makes. Links to it are gone by now, and leaving it
# behind would keep a full copy of the skill on disk with nothing pointing at it.
$stage = Join-Path (Join-Path $env:LOCALAPPDATA 'work-report\skills') $SkillName
if (Test-Path $stage) {
    if (Test-Path (Join-Path $stage 'scripts\collect.py')) {
        Try-Step "files $stage" {
            Remove-Item $stage -Recurse -Force
            Write-Host "removed files: $stage"
            $stageParent = Split-Path -Parent $stage
            if ((Test-Path $stageParent) -and -not (Get-ChildItem $stageParent -Force)) {
                Remove-Item $stageParent -Recurse -Force
                $appDir = Split-Path -Parent $stageParent
                if ((Test-Path $appDir) -and -not (Get-ChildItem $appDir -Force)) { Remove-Item $appDir -Recurse -Force }
            }
        }
    }
    else { Write-Host "kept (not installed by work-report): $stage" }
}

if ($PurgeReports) {
    if (Test-Path $root) {
        Try-Step "reports $root" {
            Remove-Item $root -Recurse -Force
            Write-Host "removed reports: $root"
        }
    }
}
else {
    Write-Host "reports kept: $root  (use -PurgeReports to delete them)"
}

$m = Get-Messages
Write-Host ''
if ($failed) {
    Write-Host '  left behind:' -ForegroundColor Yellow
    $failed | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
    Write-Host '  Close the app (Start Menu entry work-report) and run this again.' -ForegroundColor Yellow
    Write-Host ''
}
Write-Host ("  {0}" -f $(if ($m -and $m.install) { $m.install.removed } else { 'Uninstall complete' })) -ForegroundColor Green
Write-Host ''
