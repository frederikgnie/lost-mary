param(
    [switch]$DryRun,
    [switch]$NoOverwrite,
    [switch]$Verify
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DestAgents = Join-Path $HOME '.claude/agents'
$DestLib = Join-Path $HOME '.claude/agent-library'
$DestSkills = Join-Path $HOME '.claude/skills'
# Claude Code auto-loads ~/.claude/CLAUDE.md only. global-CLAUDE.md is inert
# unless that file imports it, so the installer maintains the import line.
$ClaudeMd = Join-Path $HOME '.claude/CLAUDE.md'
$ImportLine = '@~/.claude/agent-library/global-CLAUDE.md'
$Timestamp = Get-Date -Format 'yyyyMMddHHmmss'
$DriftCount = 0

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )
    $encoding = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Set-LibraryImport {
    # Additive and idempotent: never rewrites the user's own global CLAUDE.md.
    if (Test-Path -LiteralPath $ClaudeMd -PathType Leaf) {
        $existing = [System.IO.File]::ReadAllText($ClaudeMd)
        if ($existing.Contains($ImportLine)) {
            Write-Host "Unchanged: import already present in $ClaudeMd"
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
    if ((Test-Path -LiteralPath $ClaudeMd -PathType Leaf) -and
        ([System.IO.File]::ReadAllText($ClaudeMd)).Contains($ImportLine)) {
        Write-Host "OK: import present in $ClaudeMd"
    } else {
        Write-Host "DRIFT: $ClaudeMd does not import $ImportLine (global-CLAUDE.md would be inert)"
        $script:DriftCount++
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

    if ((Test-Path -LiteralPath $Target) -or ((Get-Item -LiteralPath $Target -ErrorAction SilentlyContinue) -is [System.IO.FileSystemInfo])) {
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

if (-not $Verify) {
    Invoke-Step -Description "New-Item directory '$DestAgents'" -Script { New-Item -ItemType Directory -Path $DestAgents -Force | Out-Null }
    Invoke-Step -Description "New-Item directory '$DestSkills'" -Script { New-Item -ItemType Directory -Path $DestSkills -Force | Out-Null }
    Invoke-Step -Description "New-Item directory '$DestLib/orchestration'" -Script { New-Item -ItemType Directory -Path (Join-Path $DestLib 'orchestration') -Force | Out-Null }
    Invoke-Step -Description "New-Item directory '$DestLib/scripts'" -Script { New-Item -ItemType Directory -Path (Join-Path $DestLib 'scripts') -Force | Out-Null }
}

Get-ChildItem -Path (Join-Path $RootDir 'agents') -Filter '*.md' -File | ForEach-Object {
    if ($Verify) {
        Test-ManagedFile -Source $_.FullName -Target (Join-Path $DestAgents $_.Name)
    } else {
        Install-ManagedFile -Source $_.FullName -Target (Join-Path $DestAgents $_.Name)
    }
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

if ($Verify) {
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/handoff.md') -Target (Join-Path $DestLib 'orchestration/handoff.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/handoff.schema.json') -Target (Join-Path $DestLib 'orchestration/handoff.schema.json')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/worktree-rules.md') -Target (Join-Path $DestLib 'orchestration/worktree-rules.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/team-lead.md') -Target (Join-Path $DestLib 'orchestration/team-lead.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/delegation-rules.md') -Target (Join-Path $DestLib 'orchestration/delegation-rules.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/playbooks.md') -Target (Join-Path $DestLib 'orchestration/playbooks.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/principles.md') -Target (Join-Path $DestLib 'orchestration/principles.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.py') -Target (Join-Path $DestLib 'scripts/validate-handoff.py')
    Test-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.sh') -Target (Join-Path $DestLib 'scripts/validate-handoff.sh')
    Test-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.ps1') -Target (Join-Path $DestLib 'scripts/validate-handoff.ps1')
    Test-ManagedFile -Source (Join-Path $RootDir 'scripts/guard-readonly-bash.py') -Target (Join-Path $DestLib 'scripts/guard-readonly-bash.py')
    Test-ManagedFile -Source (Join-Path $RootDir 'scripts/check-handoff-hook.py') -Target (Join-Path $DestLib 'scripts/check-handoff-hook.py')
    Test-ManagedFile -Source (Join-Path $RootDir 'capabilities.md') -Target (Join-Path $DestLib 'capabilities.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'global-CLAUDE.md') -Target (Join-Path $DestLib 'global-CLAUDE.md')
    Test-LibraryImport

    Write-Host ''
    if ($DriftCount -eq 0) {
        Write-Host 'Verify result: OK (no drift).'
        exit 0
    }
    Write-Host "Verify result: DRIFT detected in $DriftCount file(s)."
    exit 1
}

Install-ManagedFile -Source (Join-Path $RootDir 'orchestration/handoff.md') -Target (Join-Path $DestLib 'orchestration/handoff.md')
Install-ManagedFile -Source (Join-Path $RootDir 'orchestration/handoff.schema.json') -Target (Join-Path $DestLib 'orchestration/handoff.schema.json')
Install-ManagedFile -Source (Join-Path $RootDir 'orchestration/worktree-rules.md') -Target (Join-Path $DestLib 'orchestration/worktree-rules.md')
Install-ManagedFile -Source (Join-Path $RootDir 'orchestration/team-lead.md') -Target (Join-Path $DestLib 'orchestration/team-lead.md')
Install-ManagedFile -Source (Join-Path $RootDir 'orchestration/delegation-rules.md') -Target (Join-Path $DestLib 'orchestration/delegation-rules.md')
Install-ManagedFile -Source (Join-Path $RootDir 'orchestration/playbooks.md') -Target (Join-Path $DestLib 'orchestration/playbooks.md')
Install-ManagedFile -Source (Join-Path $RootDir 'orchestration/principles.md') -Target (Join-Path $DestLib 'orchestration/principles.md')
Install-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.py') -Target (Join-Path $DestLib 'scripts/validate-handoff.py')
Install-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.sh') -Target (Join-Path $DestLib 'scripts/validate-handoff.sh')
Install-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.ps1') -Target (Join-Path $DestLib 'scripts/validate-handoff.ps1')
Install-ManagedFile -Source (Join-Path $RootDir 'scripts/guard-readonly-bash.py') -Target (Join-Path $DestLib 'scripts/guard-readonly-bash.py')
Install-ManagedFile -Source (Join-Path $RootDir 'scripts/check-handoff-hook.py') -Target (Join-Path $DestLib 'scripts/check-handoff-hook.py')
Install-ManagedFile -Source (Join-Path $RootDir 'capabilities.md') -Target (Join-Path $DestLib 'capabilities.md')
Install-ManagedFile -Source (Join-Path $RootDir 'global-CLAUDE.md') -Target (Join-Path $DestLib 'global-CLAUDE.md')
Set-LibraryImport

Write-Host ''
Write-Host "Claude Code global agents installed in: $DestAgents"
Write-Host "Claude Code skills (slash commands) installed in: $DestSkills"
Write-Host "Claude Code shared orchestration assets installed in: $DestLib"
Write-Host "Operating rules imported into: $ClaudeMd"
Write-Host ''
Write-Host 'Hooks are NOT installed automatically (they live in settings.json, which'
Write-Host "this installer does not touch). Merge the 'hooks' block from"
Write-Host 'settings.example.json into ~/.claude/settings.json to enable enforcement.'
Write-Host 'Restart the current Claude Code session only if this is the first time you created the agents directory.'
