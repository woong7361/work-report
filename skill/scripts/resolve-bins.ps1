# Prints, as JSON, the python/claude/codex the tool would actually run.
# ASCII only on purpose (see _env.ps1).
#
# config.json holds these keys only as overrides, so they are usually blank.
# The settings screen shows what the automatic search found, so that a blank
# box does not read as "nothing is set up".
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File resolve-bins.ps1

param([string]$Root)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

# A missing CLI is an answer, not a failure: the screen says "not found".
function Try-Resolve {
    param([scriptblock]$Find)
    try { return & $Find } catch { return $null }
}

$out = [ordered]@{}
try {
    $root = Get-ReportRoot -Override $Root
    $cfg = Get-ReportConfig -Root $root
}
catch { $cfg = @{} }

$py = Try-Resolve { Resolve-Python -Preferred $cfg.python_bin }
$out['python'] = if ($py) {
    # 'py -3' is two words; show what would be typed
    @{ path = (@($py.Exe) + $py.Pre) -join ' '; version = $py.Version }
} else { $null }

$claude = Try-Resolve { Resolve-Claude -Preferred $cfg.claude_bin }
$out['claude'] = if ($claude) { @{ path = $claude.Path; version = $claude.Version } } else { $null }

$codex = Try-Resolve { Resolve-Codex -Preferred $cfg.codex_bin }
$out['codex'] = if ($codex) { @{ path = $codex.Path; version = $codex.Version } } else { $null }

$out | ConvertTo-Json -Depth 4 -Compress
