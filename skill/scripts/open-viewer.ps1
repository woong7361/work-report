# Opens the report viewer. This is what the Start Menu entry and every
# notification click run.
# ASCII only on purpose (see _env.ps1).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File open-viewer.ps1 [-Path <report.md>]

param(
    [string]$Path,      # a specific report; without it the newest one opens
    [string]$Root
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$root = Get-ReportRoot -Override $Root

# A viewer already on screen is the one to use. Starting another would leave a
# second server on a second port, and the browser would show a stale tab next to
# the new one.
function Get-RunningViewer {
    param([string]$Root)
    $mark = Join-Path (Join-Path $Root 'runlog') 'viewer.json'
    if (-not (Test-Path $mark)) { return 0 }
    try {
        $info = Get-Content $mark -Raw -Encoding utf8 | ConvertFrom-Json
        if (-not $info.port) { return 0 }
        if ($info.pid -and -not (Get-Process -Id $info.pid -ErrorAction SilentlyContinue)) { return 0 }
        # The port answering matters more than the process existing: the pid may
        # have been reused by something else.
        $probe = New-Object System.Net.Sockets.TcpClient
        $ok = $probe.ConnectAsync('127.0.0.1', [int]$info.port).Wait(700)
        $probe.Close()
        if ($ok) { return [int]$info.port }
    }
    catch { }
    return 0
}

$rel = ''
if ($Path) {
    try {
        $full = [System.IO.Path]::GetFullPath($Path)
        $rootSep = ([System.IO.Path]::GetFullPath($root)).TrimEnd('\') + '\'
        if ($full.StartsWith($rootSep, [System.StringComparison]::OrdinalIgnoreCase)) {
            $rel = $full.Substring($rootSep.Length).Replace('\', '/')
        }
    }
    catch { }
}

$port = Get-RunningViewer -Root $root
if ($port -gt 0) {
    $url = "http://127.0.0.1:$port/"
    if ($rel) { $url += '?path=' + [uri]::EscapeDataString($rel) }
    Start-Process $url
    return
}

$cfg = Get-ReportConfig -Root $root
$py = Resolve-Python -Preferred $cfg.python_bin
$env:WORK_REPORT_DIR = $root

# The viewer takes either a report or the report folder, and picks the newest
# report itself. A fresh install has none yet, and it opens all the same.
$target = if ($Path) { $Path } else { $root }

Start-Process -FilePath $py.Exe -WindowStyle Hidden `
    -ArgumentList ($py.Pre + @((Join-Path $PSScriptRoot 'viewer.py'), $target))
