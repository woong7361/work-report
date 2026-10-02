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

function Get-SkillRoot {
    # scripts/ lives directly under the skill directory
    return (Split-Path -Parent $PSScriptRoot)
}


foreach ($module in @('_bins.ps1', '_errors.ps1', '_toast.ps1', '_formats.ps1', '_viewer.ps1')) {
    . (Join-Path $PSScriptRoot $module)
}
