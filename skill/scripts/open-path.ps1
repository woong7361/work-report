# Opens a report from a notification click.
# ASCII only on purpose (see _env.ps1).
#
# Windows does not let a toast launch a file:// address, so the toast carries a
# private scheme and this script is what that scheme ends up running:
#     workreport://C:\Users\me\work-report\daily\2026-09\2026-09-22.md
#
# Reached through open-path.cmd, not directly: a protocol handler pointing
# straight at powershell.exe does not get invoked by a toast click.
#
# Never opens anything outside the report folder, so a link from somewhere else
# cannot use this to start arbitrary files.

param([Parameter(Mandatory)][string]$Uri)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_env.ps1"

$root = $null
function Trace([string]$line) {
    # A click that goes nowhere leaves no trace on screen, so it leaves one here.
    if (-not $root) { return }
    try {
        $dir = Join-Path (Join-Path $root 'runlog') (Get-Date -Format 'yyyy-MM')
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        "[{0}] $line" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') |
            Add-Content -Path (Join-Path $dir 'click.log') -Encoding utf8
    }
    catch { }
}

try {
    $root = [System.IO.Path]::GetFullPath((Get-ReportRoot))
    Trace "uri: $Uri"

    $raw = ($Uri -replace '^workreport:(//)?', '').Trim().TrimEnd('/')
    if ($raw -match '[?&]path=([^&]+)') { $raw = $Matches[1] }     # older encoded form
    if ($raw -like '*%*') { $raw = [uri]::UnescapeDataString($raw) }
    $full = [System.IO.Path]::GetFullPath($raw)

    # Compare with a trailing separator: a bare prefix test would also accept a
    # sibling folder whose name merely starts with the report folder's name.
    $rootSep = $root.TrimEnd('\') + '\'
    if (-not $full.StartsWith($rootSep, [System.StringComparison]::OrdinalIgnoreCase)) {
        Trace "refused (outside report folder): $full"
        return
    }
    if (-not (Test-Path -LiteralPath $full)) {
        Trace "missing: $full"
        return
    }

    # Every click lands in the viewer, report or run log alike. Which program
    # handles .md or .log differs from machine to machine, and a plain editor
    # turns the tables into noise. A viewer already running is reused.
    # A folder is the one thing the viewer cannot show, so that still goes to
    # Windows.
    if (Test-Path -LiteralPath $full -PathType Container) {
        Invoke-Item -LiteralPath $full
        Trace "folder: $full"
        return
    }
    try {
        & (Join-Path $PSScriptRoot 'open-viewer.ps1') -Path $full -Root $root
        Trace "viewer: $full"
        return
    }
    catch { Trace "viewer failed, opening with the default program: $($_.Exception.Message)" }

    Invoke-Item -LiteralPath $full
    Trace "opened: $full"
}
catch {
    # A failed click must stay silent on screen; the trace carries the reason.
    Trace "error: $($_.Exception.Message)"
}
