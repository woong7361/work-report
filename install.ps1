# work-report installer (Windows).
# ASCII only on purpose: PowerShell 5.1 reads a BOM-less script as ANSI.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -NoSchedule -Copy
#
# What it does:
#   1. checks Python 3.8+ and the agent CLI (claude, codex, or both)
#   2. creates the report folder and config.json
#   3. links (or copies) the skill into every skills folder the agent reads
#   4. registers the weekday daily run and the weekly run in Task Scheduler
#   5. runs a collection self-check

param(
    [string]$Root,                       # report folder, default ~\work-report
    [string]$Author,                     # name printed on reports, default git user.name
    [string[]]$Agents,                   # which CLIs get the skill; default: every one found
    [string]$SkillName = 'work-report',  # rename to avoid clashing with an existing skill
    [string[]]$ClaudeHome,               # override where Claude Code keeps config/skills/projects
    [string[]]$CodexHome,                # override where Codex keeps config/skills/sessions
    [string]$DailyTime = '17:30',
    [string]$WeeklyDay = 'Wednesday',
    [string]$WeeklyTime = '17:35',
    [switch]$Copy,                       # copy instead of linking
    [switch]$NoSchedule,
    [switch]$Force                       # replace an existing install without asking
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$skillSrc = Join-Path $here 'skill'
. (Join-Path $skillSrc 'scripts\_env.ps1')

function Step($n, $msg) { Write-Host "[$n] $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    OK  $msg" }
function Note($msg) { Write-Host "    --  $msg" -ForegroundColor DarkGray }

Write-Host ''
Write-Host 'work-report installer' -ForegroundColor White
Write-Host ''

# Called with -File, "claude,codex" arrives as one string, so accept either form.
$Agents = @($Agents | ForEach-Object { $_ -split '[,;\s]+' } |
    Where-Object { $_ } | ForEach-Object { $_.ToLower() })
foreach ($a in $Agents) {
    if ($a -ne 'claude' -and $a -ne 'codex') { throw "Unknown agent '$a'. Use claude, codex, or both." }
}
# Files that arrived in a zip from mail or a network share carry the Mark of the
# Web, and PowerShell refuses to run them. Clear it on the files we ship.
try { Get-ChildItem $here -Recurse -File -ErrorAction Stop | Unblock-File -ErrorAction SilentlyContinue } catch { }

# A linked install points at this folder forever. Installing from a download or
# temp folder therefore breaks the moment someone cleans it up.
if (-not $Copy) {
    foreach ($v in '\Temp\', '\Downloads\', '\AppData\Local\Temp') {
        if ($here -like "*$v*") {
            Note "Source folder looks temporary: $here"
            Note 'A linked install breaks if it is deleted. Move it somewhere permanent, or use -Copy.'
            break
        }
    }
}

# ---------------------------------------------------------------- 1. prerequisites
Step 1 'Checking prerequisites'

function Update-PathFromRegistry {
    # A freshly installed program is not on this process's PATH yet.
    $parts = @([Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User')) | Where-Object { $_ }
    $env:PATH = ($parts -join ';')
}

$msg = Get-Messages
function Say($key, $fallback) {
    if ($msg -and $msg.install -and $msg.install.$key) { return $msg.install.$key }
    return $fallback
}

function Confirm-Yes {
    # Enter means yes, but a prompt that could not be asked at all must not:
    # installing or changing someone's machine needs an actual answer.
    param([string]$Question)
    if ($Force) { return $true }
    # The question is written out, not passed to Read-Host: a Read-Host prompt
    # goes to the console host only and vanishes from a captured log.
    Write-Host ("    " + $Question) -ForegroundColor Yellow
    $a = $null
    $asked = $false
    try { $a = Read-Host '    >'; $asked = $true } catch { }
    return ($asked -and ("$a".Trim() -eq '' -or "$a".Trim() -match '^[Yy]'))
}

function Install-Agent {
    # The vendor's own installer, not winget. winget lives in a Store package,
    # and the machines that lack it are usually the ones where policy removed the
    # Store, so a winget path would fail exactly where it is needed. The official
    # installer needs no package manager and no admin rights.
    # Codex has no such installer, so it is left to the person to install.
    param([string]$Agent, [string]$Label)

    if ($Agent -ne 'claude') {
        Note ((Say 'agent_manual' 'Install {name} first:  {cmd}') `
                -replace '\{name\}', $Label -replace '\{cmd\}', 'npm install -g @openai/codex')
        return $false
    }

    Note (Say 'agent_native_cmd' 'Command to run:')
    Write-Host '      irm https://claude.ai/install.ps1 | iex' -ForegroundColor Yellow
    if (-not (Confirm-Yes (Say 'agent_offer' 'Install it now? [Y/n]'))) { return $false }
    Note ((Say 'agent_running' 'Installing {name}...') -replace '\{name\}', $Label)
    try { & ([scriptblock]::Create((Invoke-RestMethod https://claude.ai/install.ps1))) }
    catch { Note $_.Exception.Message; return $false }
    Update-PathFromRegistry
    return $true
}

function Test-AgentLogin {
    param([string]$Agent, [string]$Path)
    # These tools answer on stderr, and while the preference is Stop, Windows
    # PowerShell turns a native program's stderr into a terminating error - so
    # a signed-in account would be read as signed out.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = if ($Agent -eq 'claude') { & $Path auth status 2>&1 | Out-String }
        else { & $Path login status 2>&1 | Out-String }
    }
    catch { return $false }
    finally { $ErrorActionPreference = $prev }
    if ($Agent -eq 'claude') { return ($out -match '"loggedIn"\s*:\s*true') }
    return ($out -match 'Logged in')
}

function Confirm-AgentLogin {
    # Signing in opens a browser and cannot be done for them, so the installer
    # stops here, shows the one command to run, and waits.
    param([string]$Agent, [string]$Path, [string]$Label)
    if (Test-AgentLogin -Agent $Agent -Path $Path) {
        Ok ((Say 'login_ok' '{name} is signed in') -replace '\{name\}', $Label)
        return
    }
    $cmd = if ($Agent -eq 'claude') { 'claude auth login' } else { 'codex login' }
    Write-Host ''
    Note ((Say 'login_needed' '{name} needs sign-in.') -replace '\{name\}', $Label)
    Note (Say 'login_howto' 'Run this to sign in:')
    Write-Host ("      $cmd") -ForegroundColor Yellow
    Write-Host ''
    foreach ($try in 1, 2) {
        $ans = $null
        try { $ans = Read-Host ('    ' + (Say 'login_wait' 'Press Enter when done (s to skip)')) } catch { break }
        if ("$ans".Trim() -match '^[sS]') { break }
        if (Test-AgentLogin -Agent $Agent -Path $Path) {
            Ok ((Say 'login_ok' '{name} is signed in') -replace '\{name\}', $Label)
            return
        }
        if ($try -eq 1) { Note (Say 'login_again' 'Not signed in yet. Try again, then press Enter') }
    }
    Note ((Say 'login_skip' "Could not confirm sign-in. Run '{cmd}' later.") -replace '\{cmd\}', $cmd)
}

$py = $null
try { $py = Resolve-Python }
catch {
    # Python is the one thing this tool cannot do without, so offer to get it
    # rather than just failing. The official installer is downloaded and run for
    # the current user only: no admin rights, and nothing bundled in this folder
    # that would go stale. Bump PYTHON_VERSION when a newer release is wanted.
    Note (Say 'python_missing' 'Python 3.8+ is required.')
    if (Confirm-Yes (Say 'python_offer' 'Install it now? [Y/n]')) {
        $pyVer = '3.12.10'
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
        $url = "https://www.python.org/ftp/python/$pyVer/python-$pyVer-$arch.exe"
        $tmp = Join-Path $env:TEMP "python-$pyVer-$arch.exe"
        Note (Say 'python_running' 'Installing Python...')
        try {
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
            # Downloaded code is run here, so check who signed it before running.
            $sig = Get-AuthenticodeSignature $tmp
            if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                throw "downloaded installer is not validly signed by the Python Software Foundation (status: $($sig.Status))"
            }
            # Per-user, adds python.exe and the py launcher to PATH.
            $p = Start-Process -FilePath $tmp -Wait -PassThru -ArgumentList `
                '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0'
            if ($p.ExitCode -ne 0) { Note "python installer exit code $($p.ExitCode)" }
            Update-PathFromRegistry
            $py = Resolve-Python
            Ok (Say 'python_ok' 'Python installed')
        }
        catch { Note $_.Exception.Message }
        finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    if (-not $py) {
        Note (Say 'python_manual' 'Install Python from https://www.python.org/downloads/windows/')
        throw 'Python 3.8+ not found.'
    }
}
Ok "python $($py.Version) ($($py.Exe) $($py.Pre -join ' '))"

# playwright: used only by the PMS form filler, which is off until someone fills
# in the address. Installed here anyway, because the alternative is that the
# first person to press the button gets told to open a terminal. A failure is
# not fatal - the feature reports what is missing and still opens the form.
$pwCheck = & $py.Exe @($py.Pre + @('-c', 'import importlib.util as u; print(1 if u.find_spec(''playwright'') else 0)')) 2>$null
if ("$pwCheck".Trim() -eq '1') {
    Ok 'playwright already installed'
}
else {
    Note (Say 'pw_installing' 'Installing playwright (for filling the PMS form)...')
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $py.Exe @($py.Pre + @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', 'playwright')) 2>&1 |
            Select-Object -Last 3 | ForEach-Object { if ("$_".Trim()) { Note "$_" } }
    }
    catch { Note $_.Exception.Message }
    finally { $ErrorActionPreference = $prevEAP }
    $pwCheck = & $py.Exe @($py.Pre + @('-c', 'import importlib.util as u; print(1 if u.find_spec(''playwright'') else 0)')) 2>$null
    if ("$pwCheck".Trim() -eq '1') { Ok (Say 'pw_ok' 'playwright installed') }
    else { Note (Say 'pw_skip' 'playwright not installed - the PMS form filler stays off until it is') }
}
# Nothing asked for and nothing installed: offer the default agent rather than
# telling someone to go and find a CLI themselves.
if (-not $Agents) {
    $probe = @()
    try { Resolve-Claude | Out-Null; $probe += 'claude' } catch { }
    try { Resolve-Codex | Out-Null; $probe += 'codex' } catch { }
    if (-not $probe) {
        Note ((Say 'agent_missing' '{name} is not installed.') -replace '\{name\}', 'Claude Code')
        if (Install-Agent -Agent 'claude' -Label 'Claude Code') {
            try { Resolve-Claude | Out-Null; $probe += 'claude'; Ok ((Say 'agent_ok' '{name} installed') -replace '\{name\}', 'Claude Code') } catch { }
        }
        if (-not $probe) {
            Note ((Say 'agent_manual' 'Install {name} first:  {cmd}') -replace '\{name\}', 'Claude Code' -replace '\{cmd\}', 'irm https://claude.ai/install.ps1 | iex')
            throw 'No agent CLI found.'
        }
    }
    $Agents = $probe
}

$bins = @{}
foreach ($a in $Agents) {
    $label = if ($a -eq 'claude') { 'Claude Code' } else { 'Codex' }
    $bin = $null
    try { $bin = if ($a -eq 'claude') { Resolve-Claude } else { Resolve-Codex } }
    catch {
        Note ((Say 'agent_missing' '{name} is not installed.') -replace '\{name\}', $label)
        if (Install-Agent -Agent $a -Label $label) {
            try { $bin = if ($a -eq 'claude') { Resolve-Claude } else { Resolve-Codex } } catch { }
        }
        if (-not $bin) { throw "$label not found." }
        Ok ((Say 'agent_ok' '{name} installed') -replace '\{name\}', $label)
    }
    $bins[$a] = $bin
    Ok "$a $($bin.Version) ($($bin.Path))"
}
$gitOk = [bool](Get-Command git -ErrorAction SilentlyContinue)
if ($gitOk) { Ok 'git found' } else { Note 'git not found - commits will be missing from reports' }

# Agent homes are resolved once, here, and written into config.json. A scheduled
# task does not inherit CLAUDE_CONFIG_DIR / CODEX_HOME, so runtime must not have
# to guess them again on a machine that moved them.
$homes = @{}
foreach ($a in @('claude', 'codex')) {
    $override = if ($a -eq 'claude') { $ClaudeHome } else { $CodexHome }
    $found = Get-AgentHomes -Agent $a -Override $override
    if ($found) { $homes[$a] = $found }
    foreach ($h in $found) {
        $t = Get-TranscriptDir -AgentHome $h -Agent $a
        Ok ("{0} home: {1}{2}" -f $a, $h, $(if (Test-Path $t) { '' } else { '  (no records yet)' }))
    }
}
foreach ($a in $Agents) {
    if (-not $homes[$a]) { throw "No $a home found. Pass -$($a)Home <path>." }
}

# ---------------------------------------------------------------- 2. report folder
Step 2 'Preparing the report folder'
$root = Get-ReportRoot -Override $Root
foreach ($d in '', 'raw', 'log', 'daily', 'weekly', 'runlog', 'custom') {
    $p = if ($d) { Join-Path $root $d } else { $root }
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}
# The note that explains what custom\ is for. Never overwrite it: someone may
# have written their own reminders there, and updates must not touch this folder.
# Older versions dropped a shortcut to the newest report here. It opened the
# file with whatever handles .md, which is the thing the viewer exists to avoid.
# Only ours is removed: a shortcut is ours when it points at a report.
try {
    $sh = New-Object -ComObject WScript.Shell
    Get-ChildItem $root -Filter *.lnk -File -ErrorAction SilentlyContinue | ForEach-Object {
        $tgt = $sh.CreateShortcut($_.FullName).TargetPath
        if ($tgt -and $tgt.EndsWith('.md', [System.StringComparison]::OrdinalIgnoreCase) -and
            $tgt.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
        }
    }
}
catch { }

$note = Join-Path (Join-Path $root 'custom') 'README.md'
if (-not (Test-Path $note)) {
    $src = Join-Path $skillSrc 'templates\custom-readme.md'
    if (Test-Path $src) { Copy-Item $src $note }
}
Ok $root

$cfgPath = Join-Path $root 'config.json'
$existing = Get-ReportConfig -Root $root
if (-not $existing) {
    if (-not $Author -and $gitOk) { $Author = (git config user.name) }
    if (-not $Author) { $Author = $env:USERNAME }
    $cfg = [ordered]@{
        author        = $Author
        agent         = $Agents[0]
        notify        = $true
        submit_url    = ''
        submit_label  = ''
        claude_bin    = ''
        codex_bin     = ''
        python_bin    = ''
        mine_only     = $true
        backfill_days = 2
        retain_months = 0
        exclude_paths = @('node_modules', '\scratchpad', '\Temp\')
        exclude_repos = @()
        weekly        = [ordered]@{ end_day = $WeeklyDay; span_days = 7 }
    }
    $created = $true
}
else {
    # Keep what the user edited; only the machine facts below are refreshed.
    $cfg = [ordered]@{}
    foreach ($p in $existing.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
    if ($Author) { $cfg['author'] = $Author }
    $created = $false
}

# A config written by an older version can be missing keys added since.
$defaults = [ordered]@{
    agent = $Agents[0]; notify = $true; submit_url = ''; submit_label = ''
    claude_bin = ''; codex_bin = ''; python_bin = ''
    mine_only = $true; redact = $true; backfill_days = 2; retain_months = 0
    custom_format = $false; custom_rules = $false; custom_samples = $false
    exclude_paths = @('node_modules', '\scratchpad', '\Temp\'); exclude_repos = @()
}
foreach ($k in $defaults.Keys) { if (-not $cfg.Contains($k)) { $cfg[$k] = $defaults[$k] } }
if (-not $cfg.Contains('weekly')) { $cfg['weekly'] = [ordered]@{ end_day = $WeeklyDay; span_days = 7 } }
# The PMS form filler is optional and off until someone fills in the address.
# The keys are created empty so the settings screen has somewhere to write.
if (-not $cfg.Contains('pms')) {
    $cfg['pms'] = [ordered]@{ url = ''; project = ''; projects = @(); port = 9333; profile = ''; chrome_bin = '' }
}

foreach ($a in @('claude', 'codex')) {
    if (-not $homes[$a]) { continue }
    $cfg["${a}_homes"] = @($homes[$a])
    $cfg["${a}_dirs"] = @($homes[$a] | ForEach-Object { Get-TranscriptDir -AgentHome $_ -Agent $a } |
        Where-Object { Test-Path $_ })
}
($cfg | ConvertTo-Json -Depth 5) | Set-Content -Path $cfgPath -Encoding UTF8
Ok $(if ($created) { "config.json created (author: $($cfg.author))" } else { 'config.json updated (homes and record paths)' })

# ---------------------------------------------------------------- 3. skill
Step 3 'Installing the skill'
# A viewer left running would keep serving the version it started with, so an
# update would appear not to have happened. It also holds the files being
# replaced. Closing it costs nothing: the app reopens at the end.
if (Stop-Viewer -Root $root) { Note 'closed the running app so the new version is the one that opens' }
$installed = @{}
$skillDirs = @()
$skipped = @()

# -Copy means "do not depend on the folder this was unpacked in". Copying into
# every agent's skills folder would do that, but it leaves one independent copy
# per agent: editing one changes nothing for the others, and a run that fails
# halfway leaves them on different versions. So the files are staged once, in a
# place this tool owns, and every agent gets a link to that. One copy, one
# version, and the unpacked folder is still free to go.
# The staged folder is named after the skill, not 'skill': the runner takes the
# skill name from the folder it sits in, and would otherwise call itself 'skill'.
$linkSrc = $skillSrc
$stage = Join-Path (Join-Path $env:LOCALAPPDATA 'work-report\skills') $SkillName
if ($Copy) {
    $stageParent = Split-Path -Parent $stage
    if (-not (Test-Path $stageParent)) { New-Item -ItemType Directory -Path $stageParent -Force | Out-Null }
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    Copy-Item $skillSrc $stage -Recurse -Force
    $linkSrc = $stage
    Ok "files: $stage"
}
elseif (Test-Path (Join-Path $stage 'scripts\collect.py')) {
    # Installing linked after a -Copy install: every link is about to point at
    # the unpacked folder instead, so the staged copy would sit there with
    # nothing referring to it and quietly go stale.
    Remove-Item $stage -Recurse -Force
    Note "removed the earlier copy: $stage"
}
foreach ($a in $Agents) {
    foreach ($agentHome in $homes[$a]) {
    $skillsDir = Join-Path $agentHome 'skills'
    if (-not (Test-Path $skillsDir)) { New-Item -ItemType Directory -Path $skillsDir -Force | Out-Null }
    $target = Join-Path $skillsDir $SkillName

    if (Test-Path $target) {
        # A skill of this name may already exist and belong to someone else.
        # Overwriting it silently would destroy work, so identify it first.
        $item = Get-Item $target -Force
        # Replacing our own install is just an update, and asking about it once
        # per location would leave a half-updated machine when someone stops
        # halfway. Only a skill that is not ours is worth stopping for.
        $mine = $item.LinkType -or (Test-Path (Join-Path $target 'scripts\collect.py'))
        if (-not $mine) {
            # Someone else's skill sits here. Replacing is the default, but a
            # copy is kept first, and a no only skips this one place: stopping
            # the whole install would leave the machine half set up, with a
            # skill in one agent and no scheduled run at all.
            Note ((Say 'skill_clash' '{path} holds a different skill with the same name.') `
                    -replace '\{path\}', $target)
            if (-not (Confirm-Yes (Say 'skill_clash_ask' 'Replace it? [Y/n]'))) {
                Note (Say 'skill_clash_skip' 'Skipped. Use -SkillName <name> to install it alongside.')
                $skipped += $target
                continue
            }
            $backup = "$target.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
            try {
                Copy-Item $target $backup -Recurse -Force
                Note ((Say 'skill_clash_backup' 'Kept a copy at {path}') -replace '\{path\}', $backup)
            }
            catch { }
        }
        # a junction must be removed as a link, not as its contents
        if ($item.LinkType) { $item.Delete() } else { Remove-Item $target -Recurse -Force }
    }

    # A link is right either way: to the unpacked folder without -Copy, to the
    # staged copy with it. Only a filesystem that refuses junctions falls back.
    $linked = $false
    try {
        New-Item -ItemType Junction -Path $target -Value $linkSrc -ErrorAction Stop | Out-Null
        $linked = $true
    }
    catch { Note 'junction not available, falling back to copy' }
    if (-not $linked) { Copy-Item $linkSrc $target -Recurse -Force }
    if (-not $installed.ContainsKey($a)) { $installed[$a] = $target }
    $skillDirs += $target
    $how = if (-not $linked) { 'copied - re-run install.ps1 to update' }
    elseif ($Copy) { 'linked to the staged copy' }
    else { 'linked - keep this folder' }
    Ok ("{0}: {1} ({2})" -f $a, $target, $how)
    }
}
# An agent whose every location was skipped cannot run the skill, so it must not
# stay in the list that the schedule and the sign-in check work from.
$Agents = @($Agents | Where-Object { $installed.ContainsKey($_) })
if (-not $Agents) { throw 'The skill could not be installed anywhere. See the notes above.' }
$target = $installed[$Agents[0]]
$cfg['skill_dirs'] = @($skillDirs)
($cfg | ConvertTo-Json -Depth 5) | Set-Content -Path $cfgPath -Encoding UTF8

# ---------------------------------------------------------------- 3b. click handler
# A notification cannot open a file by itself: clicking it only hands a URI to
# Windows, which refuses file:// from a toast. So the click carries a private
# scheme, registered here for this user only - no admin rights needed.
# The handler points at a .cmd wrapper on purpose: a click coming from a toast
# does not reach a handler that points straight at powershell.exe.
$runner = Join-Path (Join-Path $target 'scripts') 'run-report.ps1'
$opener = Join-Path (Join-Path $target 'scripts') 'open-path.cmd'
$scheme = 'HKCU:\Software\Classes\workreport'
New-Item -Path $scheme -Force | Out-Null
Set-ItemProperty -Path $scheme -Name '(Default)' -Value 'URL:work-report'
Set-ItemProperty -Path $scheme -Name 'URL Protocol' -Value ''
$cmdKey = Join-Path $scheme 'shell\open\command'
New-Item -Path $cmdKey -Force | Out-Null
Set-ItemProperty -Path $cmdKey -Name '(Default)' -Value ('"{0}" "%1"' -f $opener)
Ok 'notification click handler registered'

# The identity the notifications are sent under. Without a Start Menu shortcut
# carrying this AppUserModelID, Windows drops the toast without a word: the
# report gets written and nothing appears on screen.
# The Start Menu entry opens the viewer - that is where reading, fixing and
# making a report all happen. Running a report straight from a shortcut gave
# no sign of what was going on.
$opener = Join-Path (Join-Path $target 'scripts') 'open-viewer.ps1'
$iconArg = @{}
$icon = Join-Path (Join-Path $target 'assets') 'work-report.ico'
if (Test-Path $icon) { $iconArg['Icon'] = $icon }
& (Join-Path (Join-Path $target 'scripts') 'register-appid.ps1') `
    -Target (Join-Path $PSHOME 'powershell.exe') `
    -Arguments ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $opener) `
    @iconArg | Out-Null
Ok 'notification identity registered'

# ---------------------------------------------------------------- 4. schedule
Step 4 'Registering scheduled runs'
if ($NoSchedule) {
    Note 'skipped (-NoSchedule). Run scripts\run-report.ps1 manually.'
}
else {
    $runner = Join-Path $target 'scripts\run-report.ps1'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    # Without this the task starts in C:\Windows\System32.
    $workDir = $root

    # powershell.exe on purpose, not pwsh: the desktop notification uses the
    # WinRT toast API, which Windows PowerShell reaches without extra modules.
    # Hidden: a scheduled run should not put a console window on the screen,
    # where anything the agent's own hooks print would also land.
    # -Agent is deliberately not passed: a command line argument would override
    # config.json, and switching agent there must take effect on the next run.
    $dailyAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Mode daily" `
        -WorkingDirectory $workDir
    $dailyTrigger = New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $DailyTime
    Register-ScheduledTask -TaskName 'work-report-daily' -Action $dailyAction `
        -Trigger $dailyTrigger -Settings $settings -Force `
        -Description 'Daily work report from AI conversation history and git activity' | Out-Null
    Ok "work-report-daily: Mon-Fri $DailyTime"

    $weeklyAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Mode weekly" `
        -WorkingDirectory $workDir
    $weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $WeeklyDay -At $WeeklyTime
    Register-ScheduledTask -TaskName 'work-report-weekly' -Action $weeklyAction `
        -Trigger $weeklyTrigger -Settings $settings -Force `
        -Description 'Weekly work report merged from the daily work logs' | Out-Null
    Ok "work-report-weekly: $WeeklyDay $WeeklyTime"
    Note 'Tasks run under your account while you are logged on.'
}

# ---------------------------------------------------------------- 5. sign-in
Step 5 'Checking sign-in'
foreach ($a in $Agents) {
    Confirm-AgentLogin -Agent $a -Path $bins[$a].Path -Label $(if ($a -eq 'claude') { 'Claude Code' } else { 'Codex' })
}

# ---------------------------------------------------------------- 6. self-check
Step 6 'Self-check'
& (Join-Path $target 'scripts\collect.ps1') -Check -Root $root

# The closing lines are the only thing most people read, so they are shown in
# the user's language. Script files stay ASCII; the text comes from messages.json.
$m = Get-Messages
$t = if ($m -and $m.install) { $m.install } else {
    [pscustomobject]@{ done = 'Install complete'; reports = 'reports'; skill = 'skill'
        next = 'next run'; manual = 'try it in the agent:  /work-report'
        noschedule = 'no scheduled run (-NoSchedule)'; opening = 'opening the app...'
    }
}

$next = $null
if (-not $NoSchedule) {
    try { $next = (Get-ScheduledTaskInfo -TaskName 'work-report-daily' -ErrorAction Stop).NextRunTime } catch { }
}

# A CJK character takes two columns in the console but counts as one character,
# so the format operator's padding would leave the labels ragged.
function Pad([string]$s, [int]$width) {
    $cols = 0
    foreach ($c in $s.ToCharArray()) { $cols += $(if ([int]$c -ge 0x1100) { 2 } else { 1 }) }
    return $s + (' ' * [Math]::Max(1, $width - $cols))
}

# The first report is what makes this understandable. Waiting until the next
# weekday evening to find out what it produces is a poor first impression.
# Only on a fresh install, and never unattended: -Force means nobody is watching,
# and a reinstall is an update by someone who has already seen one.
if ($created -and -not $NoSchedule -and -not $Force -and
    (Confirm-Yes (Say 'first_run_offer' 'Make one now? It takes a few minutes. [Y/n]'))) {
    Write-Host ''
    Note (Say 'first_run_going' 'Writing the first report...')
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner -Mode daily
}

Write-Host ''
Write-Host ("  {0}" -f $t.done) -ForegroundColor Green
Write-Host ''
Write-Host ("  {0}: {1}" -f (Pad $t.reports 16), $root)
Write-Host ("  {0}: {1}" -f (Pad $t.skill 16), $target)
if ($next) { Write-Host ("  {0}: {1}" -f (Pad $t.next 16), $next) }
elseif ($NoSchedule) { Write-Host ("  {0}: {1}" -f (Pad $t.next 16), $t.noschedule) }
Write-Host ''
Write-Host ("  {0}" -f $t.manual)
if ($skipped) {
    Write-Host ''
    foreach ($sk in $skipped) { Write-Host ("  {0}: {1}" -f (Say 'skipped_label' 'skipped'), $sk) -ForegroundColor DarkGray }
}
Write-Host ''

# The app is what they will actually use, so it opens once here rather than
# leaving them to find the Start Menu entry. -Force is the unattended path
# (updates, scripts): putting a browser window on screen there would be rude.
if (-not $Force) {
    Write-Host ("  {0}" -f $t.opening) -ForegroundColor DarkGray
    try { & (Join-Path $target 'scripts\open-viewer.ps1') -Root $root }
    catch { Note "could not open the app: $($_.Exception.Message)" }
    Write-Host ''
}
