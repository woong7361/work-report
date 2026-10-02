# Runtime executable discovery.

function Test-Interpreter {
    # PATH presence is not enough: the Microsoft Store stub named python.exe
    # exits without running anything. Only a real version string counts.
    param([string]$Exe, [string[]]$Pre = @())
    try {
        $out = & $Exe @($Pre + @('-c', 'import sys; print(sys.version_info[0], sys.version_info[1])')) 2>$null
    }
    catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $parts = ("$out".Trim() -split '\s+')
    if ($parts.Count -lt 2) { return $null }
    $major = [int]$parts[0]; $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 8)) { return $null }
    return [pscustomobject]@{ Exe = $Exe; Pre = $Pre; Version = "$major.$minor" }
}

function Resolve-Python {
    param([string]$Preferred)
    $candidates = @()
    if ($Preferred) { $candidates += , @($Preferred, @()) }
    if ($env:WORK_REPORT_PYTHON) { $candidates += , @($env:WORK_REPORT_PYTHON, @()) }
    $candidates += , @('py', @('-3'))          # Windows launcher: most reliable
    $candidates += , @('python3', @())
    $candidates += , @('python', @())
    foreach ($c in $candidates) {
        $found = Test-Interpreter -Exe $c[0] -Pre $c[1]
        if ($found) { return $found }
    }
    throw @'
Python 3.8+ not found.
Install it from https://www.python.org/downloads/windows/ (check "Add python.exe to PATH"),
or set WORK_REPORT_PYTHON to the full path of python.exe.
Note: a python.exe under WindowsApps is the Microsoft Store stub and does not count.
'@
}

function Test-ClaudeBin {
    param([string]$Path)
    if (-not $Path) { return $null }
    # npm puts a .ps1 shim next to the .cmd; calling the shim from a script
    # re-enters PowerShell and mangles argument quoting. Prefer the real binary.
    if ($Path -like '*.ps1') { return $null }
    try { $out = & $Path --version 2>$null } catch { return $null }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    return [pscustomobject]@{ Path = $Path; Version = "$out".Trim() }
}

function Resolve-Claude {
    param([string]$Preferred)
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    if ($env:CLAUDE_BIN) { $candidates += $env:CLAUDE_BIN }
    $cmd = Get-Command claude -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $candidates += Join-Path $env:USERPROFILE '.local\bin\claude.exe'          # native installer
    $candidates += Join-Path $env:USERPROFILE 'AppData\Roaming\npm\claude.cmd' # npm global
    $candidates += Join-Path $env:USERPROFILE '.claude\local\claude.exe'       # local installer
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Claude\claude.exe' }
    foreach ($c in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if ((Test-Path $c) -or (Get-Command $c -ErrorAction SilentlyContinue)) {
            $found = Test-ClaudeBin -Path $c
            if ($found) { return $found }
        }
    }
    throw @'
Claude Code CLI not found.
Install it (https://claude.com/claude-code) or set CLAUDE_BIN to the full path of claude.exe.
'@
}

function Resolve-Codex {
    param([string]$Preferred)
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    if ($env:CODEX_BIN) { $candidates += $env:CODEX_BIN }
    $cmd = Get-Command codex -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $candidates += Join-Path $env:USERPROFILE '.codex\bin\codex.exe'
    $candidates += Join-Path $env:USERPROFILE 'AppData\Roaming\npm\codex.cmd'
    foreach ($c in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if ((Test-Path $c) -or (Get-Command $c -ErrorAction SilentlyContinue)) {
            $found = Test-ClaudeBin -Path $c      # same check: --version must answer
            if ($found) { return $found }
        }
    }
    throw @'
Codex CLI not found.
Install it (npm i -g @openai/codex) or set CODEX_BIN to the full path of codex.exe.
'@
}

