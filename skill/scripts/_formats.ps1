# User-owned report format, writing rules and style examples.

# The report format and the writing rules the agent must follow.
#
# config.json decides the source. When "use my own" is off the shipped template
# is used, so improvements keep arriving with updates. When it is turned on the
# template is copied into the report folder once and that copy is used from then
# on - the user owns it, and nothing overwrites it again.
#
# The path is handed to the agent on the command line rather than left for it to
# find, because a file the agent decides not to read fails silently.

# A file emptied down to its heading would erase the format, so a floor applies.
# This must match CUSTOM_MIN / SAMPLE_MIN in custom_files.py: if one side accepts
# what the other rejects, the collected summary says the example was used while
# the report has no such section, and nothing on screen explains which is right.
$script:MinCustomChars = 40

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
    if ($len -lt $script:MinCustomChars) { return $template }
    return $custom
}

function Get-SampleFile {
    # Past submissions harvested from PMS, used for wording and sentence endings.
    # The tool writes the whole file and overwrites it on every fetch, so it sits
    # next to patterns.md rather than in the user's custom folder. There is no
    # shipped default: a template's own voice is not the person's voice, so when
    # nothing has been harvested the report falls back to the format file's rules.
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        $Config,
        [string]$Flag = 'custom_samples'
    )
    if (-not ($Config -and $Config.$Flag)) { return $null }
    $path = Join-Path $Root 'pms\my-submissions.md'
    if (-not (Test-Path $path)) { return $null }
    try { $body = Get-Content $path -Raw -Encoding utf8 -ErrorAction Stop }
    catch { return $null }
    if (($body -replace '\s', '').Length -lt $script:MinCustomChars) { return $null }
    return $path
}
