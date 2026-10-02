# Error logging for operations that must keep running after a logging failure.

function Write-ErrorLog {
    param([string]$Root, [string]$Where, [string]$Message)
    if (-not $Root) { return }
    try {
        $folder = Join-Path $Root 'runlog'
        if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
        $path = Join-Path $folder 'error.log'
        $line = '{0}  {1,-8} {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Where, $Message
        Add-Content -Path $path -Value $line -Encoding utf8
        # Drop anything older than a month. The file stays small enough to rewrite.
        $cutoff = (Get-Date).AddDays(-30).ToString('yyyy-MM-dd')
        $kept = @(Get-Content $path -Encoding utf8 | Where-Object {
                $_.Length -ge 10 -and $_.Substring(0, 10) -ge $cutoff })
        if ($kept.Count -lt (Get-Content $path -Encoding utf8).Count) {
            Set-Content -Path $path -Value $kept -Encoding utf8
        }
    }
    catch { }
}
