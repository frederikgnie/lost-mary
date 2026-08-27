param(
    [switch]$DryRun,
    [switch]$NoOverwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DestAgents = Join-Path $HOME '.claude/agents'
$DestLib = Join-Path $HOME '.claude/agent-library'
$Timestamp = Get-Date -Format 'yyyyMMddHHmmss'

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

Invoke-Step -Description "New-Item directory '$DestAgents'" -Script { New-Item -ItemType Directory -Path $DestAgents -Force | Out-Null }
Invoke-Step -Description "New-Item directory '$DestLib/orchestration'" -Script { New-Item -ItemType Directory -Path (Join-Path $DestLib 'orchestration') -Force | Out-Null }
Invoke-Step -Description "New-Item directory '$DestLib/scripts'" -Script { New-Item -ItemType Directory -Path (Join-Path $DestLib 'scripts') -Force | Out-Null }

Get-ChildItem -Path (Join-Path $RootDir 'agents') -Filter '*.md' -File | ForEach-Object {
    Install-ManagedFile -Source $_.FullName -Target (Join-Path $DestAgents $_.Name)
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
