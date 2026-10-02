# PMS daily-report form filler. Resolves a working Python, then runs pms.py.
# The skill calls this, never python directly: the interpreter name differs per
# machine (py, python3, python). ASCII only on purpose (see _env.ps1).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File pms.ps1 -Login
#   powershell -NoProfile -ExecutionPolicy Bypass -File pms.ps1 -Check
#   powershell -NoProfile -ExecutionPolicy Bypass -File pms.ps1 -Fetch [-Months 4]
#   powershell -NoProfile -ExecutionPolicy Bypass -File pms.ps1 -Validate <report.md>
#   powershell -NoProfile -ExecutionPolicy Bypass -File pms.ps1 -Open [-Date 2026-10-02]
#   powershell -NoProfile -ExecutionPolicy Bypass -File pms.ps1 -Fill <rows.json>
#
# Exit codes are a contract with the viewer: 0 filled, 10 opened only,
# 2 login needed, 3 config empty, 4 playwright missing, 5 chrome/port.

param(
    [switch]$Login,
    [switch]$Check,
    [string]$Fill,
    [switch]$Fetch,
    [switch]$Open,
    [string]$Validate,
    [switch]$Issues,
    [switch]$Show,
    [string]$Date,
    [int]$Months = 4,
    [int]$Keep = 10,
    [string]$Root
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$root = Get-ReportRoot -Override $Root
$cfg = Get-ReportConfig -Root $root
$py = Resolve-Python -Preferred $cfg.python_bin
$env:WORK_REPORT_DIR = $root

$script = Join-Path $PSScriptRoot 'pms.py'
$argv = @($script)
if ($Login) { $argv += '--login' }
elseif ($Check) { $argv += '--check' }
elseif ($Fetch) { $argv += @('--fetch', '--months', $Months, '--keep', $Keep) }
elseif ($Open) { $argv += '--open'; if ($Date) { $argv += @('--date', $Date) } }
elseif ($Validate) { $argv += @('--validate', $Validate) }
elseif ($Issues) { $argv += '--issues' }
elseif ($Show) { $argv += '--show' }
elseif ($Fill) { $argv += @('--fill', $Fill) }
else { throw 'Pass -Login, -Check, -Fetch, -Open, -Validate <report.md>, -Issues, or -Fill <rows.json>.' }

& $py.Exe @($py.Pre + $argv)
exit $LASTEXITCODE
