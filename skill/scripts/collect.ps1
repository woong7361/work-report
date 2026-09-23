# Collector wrapper. Resolves a working Python interpreter, then writes the
# digest into <report root>/raw/. The skill always calls this, never python directly.
# ASCII only on purpose (see _env.ps1).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File collect.ps1 -From 2026-09-21
#   powershell -NoProfile -ExecutionPolicy Bypass -File collect.ps1 -Check

param(
    [string]$From,
    [string]$To,
    [string]$Root,
    [switch]$AllAuthors,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$root = Get-ReportRoot -Override $Root
$cfg = Get-ReportConfig -Root $root
$py = Resolve-Python -Preferred $cfg.python_bin
$script = Join-Path $PSScriptRoot 'collect.py'
$env:WORK_REPORT_DIR = $root

if ($Check) {
    & $py.Exe @($py.Pre + @($script, '--check'))
    exit $LASTEXITCODE
}

if (-not $From) { $From = Get-Date -Format 'yyyy-MM-dd' }
if (-not $To) { $To = $From }
$name = if ($From -eq $To) { $From } else { "$From`_$To" }
# Files are filed by month so a folder stays readable as years accumulate.
# A range that crosses a month boundary is filed under the month it ends in.
$month = $To.Substring(0, 7)

foreach ($d in 'raw', 'log', 'daily', 'weekly', 'runlog') {
    $p = Join-Path (Join-Path $root $d) $month
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

$md = Join-Path $root "raw\$month\$name.md"
$json = Join-Path $root "raw\$month\$name.json"
$argv = @($script, '--from', $From, '--to', $To, '--md', $md, '--json', $json)
if ($AllAuthors) { $argv += '--all-authors' }

& $py.Exe @($py.Pre + $argv)
if ($LASTEXITCODE -ne 0) { throw "collect.py failed with exit code $LASTEXITCODE" }

Write-Output "raw markdown : $md"
Write-Output "raw json     : $json"
Write-Output "report root  : $root"
