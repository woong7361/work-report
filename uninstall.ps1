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

foreach ($name in 'work-report-daily', 'work-report-weekly') {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "removed scheduled task: $name"
    }
}

foreach ($k in 'HKCU:\Software\Classes\workreport',
    'HKCU:\Software\Classes\AppUserModelId\WorkReport.Notify') {
    if (Test-Path $k) { Remove-Item $k -Recurse -Force; Write-Host "removed registry key: $k" }
}
$lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\work-report.lnk'
if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host 'removed Start Menu entry' }

# An agent can have several homes and the install puts the skill in all of them,
# so removing only the one this shell points at would leave copies behind.
# config.json lists what was installed; the homes are checked as well in case it
# was edited or the skill was installed by an older version.
$root = Get-ReportRoot -Override $Root
$cfg = Get-ReportConfig -Root $root
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
    if ($item.LinkType) { $item.Delete() } else { Remove-Item $target -Recurse -Force }
    Write-Host "removed skill: $target"
}

if ($PurgeReports) {
    if (Test-Path $root) {
        Remove-Item $root -Recurse -Force
        Write-Host "removed reports: $root"
    }
}
else {
    Write-Host "reports kept: $root  (use -PurgeReports to delete them)"
}

$m = Get-Messages
Write-Host ''
Write-Host ("  {0}" -f $(if ($m -and $m.install) { $m.install.removed } else { 'Uninstall complete' })) -ForegroundColor Green
Write-Host ''
