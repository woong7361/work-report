# Viewer process lifecycle.

function Stop-Viewer {
    <#
      Stops the viewer if one is running for this report folder.

      The viewer holds the page and the code it was started with, and
      open-viewer.ps1 reuses a running one instead of starting a second
      server. Replacing the files under a running viewer therefore leaves
      the old screen on display, with a reload changing nothing - the new
      version looks like it did not install. Stopping it here means the
      next open starts from the files that were just written.
    #>
    param([Parameter(Mandatory = $true)][string]$Root)

    $mark = Join-Path (Join-Path $Root 'runlog') 'viewer.json'
    if (-not (Test-Path $mark)) { return $false }
    try { $info = Get-Content $mark -Raw -Encoding utf8 | ConvertFrom-Json }
    catch { return $false }
    if (-not $info.pid) { return $false }
    if (-not (Get-Process -Id $info.pid -ErrorAction SilentlyContinue)) {
        Remove-Item $mark -Force -ErrorAction SilentlyContinue
        return $false
    }
    # The pid may have been reused by something else since it was written,
    # and stopping an unrelated process would be far worse than a stale page.
    $mine = $false
    try {
        $line = (Get-CimInstance Win32_Process -Filter "ProcessId = $($info.pid)" -ErrorAction Stop).CommandLine
        $mine = $line -and ($line -match 'viewer\.py')
    }
    catch { }
    if (-not $mine) { return $false }
    try { Stop-Process -Id $info.pid -Force -ErrorAction Stop }
    catch { return $false }
    Remove-Item $mark -Force -ErrorAction SilentlyContinue
    return $true
}
