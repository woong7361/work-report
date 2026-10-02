# Shared environment resolution for the work-report skill.
# ASCII only: PowerShell 5.1 reads a BOM-less script as ANSI, so non-ASCII text
# in a script file breaks on machines with a different code page.

function Get-ReportRoot {
    param([string]$Override)
    if ($Override) { return $Override }
    if ($env:WORK_REPORT_DIR) { return $env:WORK_REPORT_DIR }
    return (Join-Path $env:USERPROFILE 'work-report')
}

function Get-ReportConfig {
    param([string]$Root)
    $path = Join-Path $Root 'config.json'
    if (Test-Path $path) {
        try { return Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { Write-Warning "config.json parse failed: $path" }
    }
    return $null
}

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

function Get-AgentHomes {
    <#
      Where an agent keeps its config, skills and session records.
      This differs per machine and per launcher, and a scheduled task does not
      inherit the environment variable that moved it, so the install resolves
      the homes once and writes them into config.json. Runtime reads them back.
    #>
    param(
        [Parameter(Mandatory)][ValidateSet('claude', 'codex')][string]$Agent,
        [string[]]$Override,
        $Config
    )
    $leaf = if ($Agent -eq 'claude') { '.claude' } else { '.codex' }
    $envVar = if ($Agent -eq 'claude') { $env:CLAUDE_CONFIG_DIR } else { $env:CODEX_HOME }
    $saved = if ($Config) { $Config."${Agent}_homes" } else { $null }

    $found = @()
    foreach ($c in @($Override) + @($saved) + @($envVar) + @(Join-Path $env:USERPROFILE $leaf)) {
        if (-not $c) { continue }
        try { $full = [System.IO.Path]::GetFullPath($c) } catch { continue }
        if ((Test-Path $full) -and ($found -notcontains $full)) { $found += $full }
    }
    return $found
}

function Get-TranscriptDir {
    param([Parameter(Mandatory)][string]$AgentHome,
        [Parameter(Mandatory)][ValidateSet('claude', 'codex')][string]$Agent)
    $leaf = if ($Agent -eq 'claude') { 'projects' } else { 'sessions' }
    return (Join-Path $AgentHome $leaf)
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

function Get-Messages {
    # Notification text lives in a UTF-8 file so the scripts can stay ASCII:
    # a BOM-less script with non-ASCII text is decoded with the machine's code
    # page and breaks on someone else's computer.
    $path = Join-Path $PSScriptRoot 'messages.json'
    try { return Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return $null }
}

function Get-SkillRoot {
    # scripts/ lives directly under the skill directory
    return (Split-Path -Parent $PSScriptRoot)
}

# The report format and the writing rules the agent must follow.
#
# config.json decides the source. When "use my own" is off the shipped template
# is used, so improvements keep arriving with updates. When it is turned on the
# template is copied into the report folder once and that copy is used from then
# on - the user owns it, and nothing overwrites it again.
#
# The path is handed to the agent on the command line rather than left for it to
# find, because a file the agent decides not to read fails silently.
# Unexpected failures from every part land in one file, kept for a month.
# The run log holds what happened; this holds what went wrong, so a question
# weeks later has one place to look. Never shown in the viewer.
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

function Get-FormatFile {
    param(
        [Parameter(Mandatory = $true)][string]$Name,   # report-format.md | writing-rules.md
        [Parameter(Mandatory = $true)][string]$Root,
        $Config,
        [string]$Flag
    )
    $template = Join-Path (Join-Path (Get-SkillRoot) 'templates') $Name
    if (-not (Test-Path $template)) { return $null }

    $mine = $false
    if ($Flag -and $Config -and $null -ne $Config.$Flag) { $mine = [bool]$Config.$Flag }
    if (-not $mine) { return $template }

    $custom = Join-Path (Join-Path $Root 'custom') $Name
    if (-not (Test-Path $custom)) {
        # Turning the switch on is the request for a copy to edit. Seed it once
        # so nobody starts from a blank file.
        $folder = Split-Path -Parent $custom
        if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
        try { Copy-Item $template $custom } catch { return $template }
    }
    # An empty or barely filled file would erase the format. Fall back, loudly.
    try { $len = ((Get-Content $custom -Raw -ErrorAction Stop) -replace '\s', '').Length }
    catch { return $template }
    if ($len -lt 40) { return $template }
    return $custom
}

function Get-SampleFile {
    # Past reports pasted by the user, used as a style example. Unlike the
    # format and rules files this one has no default to fall back to: the
    # template is a guide, and following it would teach the guide's voice.
    # So it counts only once enough text sits below the paste marker to read
    # a way of writing from.
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        $Config,
        [string]$Flag = 'custom_samples'
    )
    if (-not ($Config -and $Config.$Flag)) { return $null }

    $name = 'my-reports.md'
    $custom = Join-Path (Join-Path $Root 'custom') $name
    if (-not (Test-Path $custom)) {
        $template = Join-Path (Join-Path (Get-SkillRoot) 'templates') $name
        if (-not (Test-Path $template)) { return $null }
        $folder = Split-Path -Parent $custom
        if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
        try { Copy-Item $template $custom } catch { return $null }
        return $null     # freshly seeded: nothing is pasted yet
    }
    # The same encoding collect.py reads it with, so both sides count the
    # same characters rather than one of them counting mojibake.
    try { $body = Get-Content $custom -Raw -Encoding utf8 -ErrorAction Stop }
    catch { return $null }

    # Only what sits below the marker is the example: the guide above it
    # clears any length floor on its own.
    $mark = '<!-- PASTE BELOW -->'
    $at = $body.LastIndexOf($mark)
    if ($at -ge 0) { $body = $body.Substring($at + $mark.Length) }
    # Must match SAMPLE_MIN in collect.py. If one side accepts what the other
    # rejects, the collected summary and the report disagree about whether the
    # example was used, and nothing on screen explains which is right.
    $minChars = 40
    if (($body -replace '\s', '').Length -lt $minChars) { return $null }
    return $custom
}

function Hide-Toast {
    # Clears an earlier toast so a finished run does not leave "in progress"
    # sitting in the notification centre.
    param([string]$Tag = 'work-report', [string]$Group = 'work-report')
    foreach ($appId in @('WorkReport.Notify',
            '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe')) {
        try {
            [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
            [Windows.UI.Notifications.ToastNotificationManager]::History.Remove($Tag, $Group, $appId)
        }
        catch { }
    }
}

function Show-Toast {
    <#
      Windows notification with no external module.
      Layered on purpose: the toast API is not available in every host, so a
      failure to notify must never fail the run. Each layer is tried in turn
      and the last one just returns.
        1. WinRT toast      - Windows 10/11 under Windows PowerShell
        2. tray balloon     - any Windows with WinForms
        3. nothing          - headless or blocked; the run log still records it
      Same Tag/Group on purpose: the finished toast replaces the running one.
    #>
    param(
        [Parameter(Mandatory)][string]$Title,
        [string]$Message = '',
        [ValidateSet('info', 'error')][string]$Kind = 'info',
        [string]$Tag = 'work-report',
        [string]$OpenPath           # clicking the toast opens this
    )

    if ($env:WORK_REPORT_NO_TOAST -eq '1') { return 'disabled' }

    # Windows refuses file:// from a toast, so the click carries a private
    # scheme that install.ps1 registers. The path is NOT percent-encoded and the
    # scheme runs a .cmd wrapper: a toast click does not reach a handler that
    # points straight at powershell.exe, and does not survive an encoded path.
    function ToUri([string]$p) { if ($p) { "workreport://$p" } else { '' } }
    function Esc([string]$s) { [System.Security.SecurityElement]::Escape($s) }

    try {
        [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]

        $body = '<toast activationType="protocol" launch="{0}"><visual><binding template="ToastText02"><text id="1">{1}</text><text id="2">{2}</text></binding></visual></toast>' -f `
        (Esc (ToUri $OpenPath)), (Esc $Title), (Esc $Message)

        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($body)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        $toast.Tag = $Tag
        $toast.Group = 'work-report'
        # Our own AppId, registered by install.ps1. Windows delivers a toast
        # under an application identity and drops it silently when that identity
        # has no Start Menu shortcut carrying the matching AppUserModelID, so
        # borrowing the built-in PowerShell identity fails on any machine whose
        # shortcut is gone - report written, notification never seen. That one
        # stays only as a fallback.
        foreach ($appId in @('WorkReport.Notify',
                '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe')) {
            try {
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
                return 'toast'
            }
            catch { }
        }
        return 'none'
    }
    catch { }

    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $icon = New-Object System.Windows.Forms.NotifyIcon
        $icon.Icon = if ($Kind -eq 'error') { [System.Drawing.SystemIcons]::Error }
        else { [System.Drawing.SystemIcons]::Information }
        $icon.Visible = $true
        $icon.ShowBalloonTip(5000, $Title, $Message, $(if ($Kind -eq 'error') { 'Error' } else { 'Info' }))
        Start-Sleep -Seconds 5          # the balloon dies with the icon
        $icon.Dispose()
        return 'balloon'
    }
    catch { }

    return 'none'
}
