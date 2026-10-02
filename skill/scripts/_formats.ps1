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
# Unexpected failures from every part land in one file, kept for a month.
# The run log holds what happened; this holds what went wrong, so a question
# weeks later has one place to look. Never shown in the viewer.

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

