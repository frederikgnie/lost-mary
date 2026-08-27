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
$Timestamp = Get-Date -Format 'yyyyMMddHHmmss'
$DriftCount = 0

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

if ($Verify) {
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/handoff.md') -Target (Join-Path $DestLib 'orchestration/handoff.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/handoff.schema.json') -Target (Join-Path $DestLib 'orchestration/handoff.schema.json')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/worktree-rules.md') -Target (Join-Path $DestLib 'orchestration/worktree-rules.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/team-lead.md') -Target (Join-Path $DestLib 'orchestration/team-lead.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'orchestration/delegation-rules.md') -Target (Join-Path $DestLib 'orchestration/delegation-rules.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.py') -Target (Join-Path $DestLib 'scripts/validate-handoff.py')
    Test-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.sh') -Target (Join-Path $DestLib 'scripts/validate-handoff.sh')
    Test-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.ps1') -Target (Join-Path $DestLib 'scripts/validate-handoff.ps1')
    Test-ManagedFile -Source (Join-Path $RootDir 'capabilities.md') -Target (Join-Path $DestLib 'capabilities.md')
    Test-ManagedFile -Source (Join-Path $RootDir 'global-CLAUDE.md') -Target (Join-Path $DestLib 'global-CLAUDE.md')

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
Install-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.py') -Target (Join-Path $DestLib 'scripts/validate-handoff.py')
Install-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.sh') -Target (Join-Path $DestLib 'scripts/validate-handoff.sh')
Install-ManagedFile -Source (Join-Path $RootDir 'scripts/validate-handoff.ps1') -Target (Join-Path $DestLib 'scripts/validate-handoff.ps1')
Install-ManagedFile -Source (Join-Path $RootDir 'capabilities.md') -Target (Join-Path $DestLib 'capabilities.md')
Install-ManagedFile -Source (Join-Path $RootDir 'global-CLAUDE.md') -Target (Join-Path $DestLib 'global-CLAUDE.md')

Write-Host ''
Write-Host "Claude Code global agents installed in: $DestAgents"
Write-Host "Claude Code shared orchestration assets installed in: $DestLib"
Write-Host 'Restart the current Claude Code session only if this is the first time you created the agents directory.'
