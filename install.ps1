param(
    [switch]$DryRun,
    [switch]$NoOverwrite,
    [switch]$Verify
)

# Install the agent library (v2) into ~/.claude. See README.md.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DestAgents = Join-Path $HOME '.claude/agents'
$DestLib = Join-Path $HOME '.claude/agent-library'
$DestSkills = Join-Path $HOME '.claude/skills'
$Settings = Join-Path $HOME '.claude/settings.json'
# Claude Code auto-loads ~/.claude/CLAUDE.md only. global-CLAUDE.md is inert
# unless that file imports it, so the installer maintains the import line.
$ClaudeMd = Join-Path $HOME '.claude/CLAUDE.md'
$ImportLine = '@~/.claude/agent-library/global-CLAUDE.md'
$Timestamp = Get-Date -Format 'yyyyMMddHHmmss'
$DriftCount = 0

# What v2 manages under ~/.claude/agent-library.
$LibFiles = @('scripts/pycheck.py', 'scripts/check-evidence.py', 'global-CLAUDE.md', 'capabilities.md')
# What v1 installed and v2 no longer ships. Retired by renaming, never deleted.
$RetiredAgents = @('architect', 'researcher', 'implementer', 'debugger', 'tester', 'reviewer', 'security-reviewer')
$RetiredLib = @('orchestration', 'scripts/check-handoff-hook.py', 'scripts/guard-readonly-bash.py', 'scripts/validate-handoff.py', 'scripts/validate-handoff.sh', 'scripts/validate-handoff.ps1')
$V1HookPattern = 'check-handoff-hook\.py|guard-readonly-bash\.py'

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )
    $encoding = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Get-ImportLineCount {
    param([Parameter(Mandatory = $true)][string]$Path)
    # Exact-line count, not substring: a mention of the path inside prose must
    # not satisfy the import requirement. ReadAllLines strips CR/LF endings.
    $n = 0
    foreach ($l in [System.IO.File]::ReadAllLines($Path)) {
        if ($l -eq $ImportLine) { $n++ }
    }
    return $n
}

function Set-LibraryImport {
    # Additive, idempotent and self-healing: converge to exactly one import
    # line without ever rewriting the user's own content.
    if (Test-Path -LiteralPath $ClaudeMd -PathType Leaf) {
        $existing = [System.IO.File]::ReadAllText($ClaudeMd)
        $count = Get-ImportLineCount -Path $ClaudeMd
        if ($count -eq 1) {
            Write-Host "Unchanged: import already present in $ClaudeMd"
            return
        }
        if ($count -gt 1) {
            if ($NoOverwrite) {
                Write-Host "WARNING: import line appears $count times in $ClaudeMd (-NoOverwrite: leaving as is)"
                return
            }
            if ($DryRun) {
                Write-Host "[dry-run] dedupe import line in $ClaudeMd ($count -> 1, after backup)"
                return
            }
            $backup = "$ClaudeMd.backup.$Timestamp"
            Write-Host "Backing up existing $ClaudeMd -> $backup"
            Copy-Item -LiteralPath $ClaudeMd -Destination $backup -Force
            $newline = "`n"
            if ($existing.Contains("`r`n")) { $newline = "`r`n" }
            $seen = $false
            $kept = foreach ($l in [System.IO.File]::ReadAllLines($ClaudeMd)) {
                if ($l -eq $ImportLine) {
                    if ($seen) { continue }
                    $seen = $true
                }
                $l
            }
            Write-Utf8NoBom -Path $ClaudeMd -Text (($kept -join $newline) + $newline)
            Write-Host "Deduplicated import line in $ClaudeMd ($count -> 1)"
            return
        }
        if ($NoOverwrite) {
            Write-Host "Skipped (-NoOverwrite): $ClaudeMd not modified"
            Write-Host "MANUAL ACTION REQUIRED - add this line to $ClaudeMd or the library stays inert:"
            Write-Host "    $ImportLine"
            return
        }
        if ($DryRun) {
            Write-Host "[dry-run] append import line to $ClaudeMd (after backup)"
            return
        }
        $backup = "$ClaudeMd.backup.$Timestamp"
        Write-Host "Backing up existing $ClaudeMd -> $backup"
        Copy-Item -LiteralPath $ClaudeMd -Destination $backup -Force
        $separator = "`n"
        if (-not $existing.EndsWith("`n")) { $separator = "`n`n" }
        Write-Utf8NoBom -Path $ClaudeMd -Text ($existing + $separator + $ImportLine + "`n")
        Write-Host "Appended agent-library import to $ClaudeMd"
        return
    }
    if ($DryRun) {
        Write-Host "[dry-run] create $ClaudeMd with import line"
        return
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $ClaudeMd) -Force | Out-Null
    Write-Utf8NoBom -Path $ClaudeMd -Text ("# Global Claude Code Instructions`n`n" + $ImportLine + "`n")
    Write-Host "Created $ClaudeMd with agent-library import"
}

function Test-LibraryImport {
    if (-not (Test-Path -LiteralPath $ClaudeMd -PathType Leaf)) {
        Write-Host "DRIFT: $ClaudeMd does not exist (global-CLAUDE.md would be inert)"
        $script:DriftCount++
        return
    }
    $count = Get-ImportLineCount -Path $ClaudeMd
    if ($count -eq 1) {
        Write-Host "OK: import present exactly once in $ClaudeMd"
    } elseif ($count -eq 0) {
        Write-Host "DRIFT: $ClaudeMd does not import $ImportLine (global-CLAUDE.md would be inert)"
        $script:DriftCount++
    } else {
        Write-Host "DRIFT: import line appears $count times in $ClaudeMd (run the installer to dedupe)"
        $script:DriftCount++
    }
}

function Test-Settings {
    # Read-only. The installer never edits settings.json, but -Verify should say
    # whether the hooks are wired and whether v1 entries linger.
    if (-not (Test-Path -LiteralPath $Settings -PathType Leaf)) {
        Write-Host "NOTE: $Settings not found - hooks are not enabled (see settings.example.windows.json)"
        return
    }
    $text = [System.IO.File]::ReadAllText($Settings)
    if ($text -match $V1HookPattern) {
        Write-Host "STALE: $Settings still references v1 hook scripts - replace the hooks block with settings.example.windows.json"
        $script:DriftCount++
    }
    if ($text -match 'pycheck\.py') {
        Write-Host "OK: $Settings wires pycheck.py"
    } else {
        Write-Host "NOTE: $Settings does not wire pycheck.py - hooks not enabled (merge settings.example.windows.json)"
    }
}

function Invoke-Step {
    param([scriptblock]$Script, [string]$Description)
    if ($DryRun) {
        Write-Host "[dry-run] $Description"
    } else {
        & $Script
    }
}

function Install-ManagedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (Test-Path -LiteralPath $Target) {
        $same = $false
        if (Test-Path -LiteralPath $Target -PathType Leaf) {
            try {
                $srcHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Source).Hash
                $dstHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Target).Hash
                $same = ($srcHash -eq $dstHash)
            } catch {
                $same = $false
            }
        }
        if ($same) {
            Write-Host "Unchanged: $Target"
            return
        }
        if ($NoOverwrite) {
            Write-Host "Skipped (exists): $Target"
            return
        }
        $backup = "$Target.backup.$Timestamp"
        Write-Host "Backing up existing $Target -> $backup"
        Invoke-Step -Description "Move-Item '$Target' '$backup'" -Script { Move-Item -LiteralPath $Target -Destination $backup -Force }
    }
    Invoke-Step -Description "Copy-Item '$Source' '$Target'" -Script { Copy-Item -LiteralPath $Source -Destination $Target -Force }
    if ($DryRun) {
        Write-Host "Planned install $([System.IO.Path]::GetFileName($Target))"
    } else {
        Write-Host "Installed $([System.IO.Path]::GetFileName($Target))"
    }
}

function Test-ManagedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (-not (Test-Path -LiteralPath $Target)) {
        Write-Host "MISSING: $Target"
        $script:DriftCount++
        return
    }
    try {
        $srcHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Source).Hash
        $dstHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Target).Hash
        if ($srcHash -eq $dstHash) {
            Write-Host "OK: $Target"
        } else {
            Write-Host "DRIFT: $Target"
            $script:DriftCount++
        }
    } catch {
        Write-Host "DRIFT: $Target"
        $script:DriftCount++
    }
}

function Retire-Path {
    param([Parameter(Mandatory = $true)][string]$Target)
    if (-not (Test-Path -LiteralPath $Target)) { return }
    if ($NoOverwrite) {
        Write-Host "WARNING: v1 file still installed: $Target (-NoOverwrite: leaving as is)"
        return
    }
    $moved = "$Target.retired.$Timestamp"
    Write-Host "Retiring v1 file: $Target -> $moved"
    Invoke-Step -Description "Move-Item '$Target' '$moved'" -Script { Move-Item -LiteralPath $Target -Destination $moved -Force }
}

function Test-Retired {
    param([Parameter(Mandatory = $true)][string]$Target)
    if (Test-Path -LiteralPath $Target) {
        Write-Host "STALE: $Target (v1 file still installed; run the installer to retire it)"
        $script:DriftCount++
    }
}

if (-not $Verify) {
    Invoke-Step -Description "New-Item directory '$DestAgents'" -Script { New-Item -ItemType Directory -Path $DestAgents -Force | Out-Null }
    Invoke-Step -Description "New-Item directory '$DestSkills'" -Script { New-Item -ItemType Directory -Path $DestSkills -Force | Out-Null }
    Invoke-Step -Description "New-Item directory '$DestLib/scripts'" -Script { New-Item -ItemType Directory -Path (Join-Path $DestLib 'scripts') -Force | Out-Null }
}

Get-ChildItem -Path (Join-Path $RootDir 'agents') -Filter '*.md' -File | ForEach-Object {
    if ($Verify) {
        Test-ManagedFile -Source $_.FullName -Target (Join-Path $DestAgents $_.Name)
    } else {
        Install-ManagedFile -Source $_.FullName -Target (Join-Path $DestAgents $_.Name)
    }
}
foreach ($name in $RetiredAgents) {
    if ($Verify) { Test-Retired -Target (Join-Path $DestAgents "$name.md") }
    else { Retire-Path -Target (Join-Path $DestAgents "$name.md") }
}

# Skills become slash commands (~/.claude/skills/<name>/SKILL.md -> /<name>).
$skillRoot = Join-Path $RootDir 'skills'
if (Test-Path -LiteralPath $skillRoot) {
    Get-ChildItem -Path $skillRoot -Directory | ForEach-Object {
        $source = Join-Path $_.FullName 'SKILL.md'
        if (-not (Test-Path -LiteralPath $source)) { return }
        $targetDir = Join-Path $DestSkills $_.Name
        $target = Join-Path $targetDir 'SKILL.md'
        if ($Verify) {
            Test-ManagedFile -Source $source -Target $target
        } else {
            Invoke-Step -Description "New-Item directory '$targetDir'" -Script { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }
            Install-ManagedFile -Source $source -Target $target
        }
    }
}

foreach ($rel in $LibFiles) {
    if ($Verify) { Test-ManagedFile -Source (Join-Path $RootDir $rel) -Target (Join-Path $DestLib $rel) }
    else { Install-ManagedFile -Source (Join-Path $RootDir $rel) -Target (Join-Path $DestLib $rel) }
}
foreach ($rel in $RetiredLib) {
    if ($Verify) { Test-Retired -Target (Join-Path $DestLib $rel) }
    else { Retire-Path -Target (Join-Path $DestLib $rel) }
}

if ($Verify) {
    Test-LibraryImport
    Test-Settings
    Write-Host ''
    if ($DriftCount -eq 0) {
        Write-Host 'Verify result: OK (no drift).'
        exit 0
    }
    Write-Host "Verify result: DRIFT detected in $DriftCount item(s)."
    exit 1
}

Set-LibraryImport

Write-Host ''
Write-Host "Agents installed in:          $DestAgents  (explore, implement, review)"
Write-Host "Skills installed in:          $DestSkills  (/lost-mary, /validate, /pr)"
Write-Host "Hook scripts + rules in:      $DestLib"
Write-Host "Operating rules imported by:  $ClaudeMd"
Write-Host ''
Write-Host 'Hooks are NOT installed automatically (they live in settings.json, which this'
Write-Host "installer never touches). Merge the 'hooks' block from settings.example.windows.json"
Write-Host 'into ~/.claude/settings.json (use an absolute interpreter path: `python` on PATH is'
Write-Host 'often the Microsoft Store stub), then restart Claude Code. Run .\install.ps1 -Verify'
Write-Host 'afterwards; it reports whether the hooks are wired.'
