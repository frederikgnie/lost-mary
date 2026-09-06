param(
    [switch]$DryRun,
    [switch]$NoOverwrite,
    [switch]$Verify
)

# Install the agent library (v2) into ~/.claude. See README.md.
# Exit codes: 0 ok; 1 drift (-Verify); 3 install incomplete (-NoOverwrite left
# the library inert or v1 files in place).
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
$IncompleteCount = 0

# What v2 manages under ~/.claude/agent-library.
$LibFiles = @('scripts/pycheck.py', 'scripts/check-evidence.py', 'scripts/no-ask.py', 'scripts/no-punt.py', 'scripts/friction.py', 'scripts/ledger.py', 'scripts/check-spawn.py', 'scripts/permit.py', 'global-CLAUDE.md', 'capabilities.md')
# What v1 installed and v2 no longer ships. Retired by renaming, never deleted.
# Agent files are retired ONLY when their content is provably v1 (every v1 role
# referenced the handoff schema); a user's own reviewer.md is left alone.
$RetiredAgents = @('architect', 'researcher', 'implementer', 'debugger', 'tester', 'reviewer', 'security-reviewer')
$V1AgentSignature = 'agent-library/orchestration/handoff.schema.json'
$RetiredLib = @('orchestration', 'scripts/check-handoff-hook.py', 'scripts/guard-readonly-bash.py', 'scripts/validate-handoff.py', 'scripts/validate-handoff.sh', 'scripts/validate-handoff.ps1')
$V1HookPattern = 'check-handoff-hook\.py|guard-readonly-bash\.py'

function Get-FileEncoding {
    # Detect how to write a text file back without changing its bytes:
    # UTF-8 with BOM, strict UTF-8, else Windows-1252 (the common legacy case).
    param([Parameter(Mandatory = $true)][string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        return New-Object System.Text.UTF8Encoding $true
    }
    $strict = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        [void]$strict.GetString($bytes)
        return New-Object System.Text.UTF8Encoding $false
    } catch {
        return [System.Text.Encoding]::GetEncoding(1252)
    }
}

function Get-ImportLineCount {
    param([Parameter(Mandatory = $true)][string]$Path)
    # Exact-line count, not substring: a mention of the path inside prose must
    # not satisfy the import requirement. ReadAllLines strips CR/LF endings.
    $n = 0
    $enc = Get-FileEncoding -Path $Path
    foreach ($l in [System.IO.File]::ReadAllLines($Path, $enc)) {
        if ($l -eq $ImportLine) { $n++ }
    }
    return $n
}

function Set-LibraryImport {
    # Additive, idempotent and self-healing: converge to exactly one import
    # line. The user's own bytes are appended to or filtered line-by-line in
    # their original encoding, never re-encoded.
    if (Test-Path -LiteralPath $ClaudeMd -PathType Leaf) {
        $enc = Get-FileEncoding -Path $ClaudeMd
        $existing = [System.IO.File]::ReadAllText($ClaudeMd, $enc)
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
            $kept = foreach ($l in [System.IO.File]::ReadAllLines($ClaudeMd, $enc)) {
                if ($l -eq $ImportLine) {
                    if ($seen) { continue }
                    $seen = $true
                }
                $l
            }
            [System.IO.File]::WriteAllText($ClaudeMd, (($kept -join $newline) + $newline), $enc)
            Write-Host "Deduplicated import line in $ClaudeMd ($count -> 1)"
            return
        }
        if ($NoOverwrite) {
            Write-Host "Skipped (-NoOverwrite): $ClaudeMd not modified"
            Write-Host "MANUAL ACTION REQUIRED - add this line to $ClaudeMd or the library stays inert:"
            Write-Host "    $ImportLine"
            $script:IncompleteCount++
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
        if ($existing.Length -gt 0 -and -not $existing.EndsWith("`n")) { $separator = "`n`n" }
        # Append ASCII bytes only; the existing content is not rewritten.
        [System.IO.File]::AppendAllText($ClaudeMd, ($separator + $ImportLine + "`n"), [System.Text.Encoding]::ASCII)
        Write-Host "Appended agent-library import to $ClaudeMd"
        return
    }
    if ($DryRun) {
        Write-Host "[dry-run] create $ClaudeMd with import line"
        return
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $ClaudeMd) -Force | Out-Null
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($ClaudeMd, ("# Global Claude Code Instructions`n`n" + $ImportLine + "`n"), $utf8)
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

function Get-HookCommands {
    param($Hooks, [string]$EventName)
    $out = @()
    if ($null -eq $Hooks) { return $out }
    $event = $Hooks.PSObject.Properties[$EventName]
    if ($null -eq $event -or $null -eq $event.Value) { return $out }
    foreach ($entry in @($event.Value)) {
        $matcher = ''
        if ($entry.PSObject.Properties['matcher']) { $matcher = [string]$entry.matcher }
        $inner = $entry.PSObject.Properties['hooks']
        if ($null -eq $inner -or $null -eq $inner.Value) { continue }
        foreach ($h in @($inner.Value)) {
            $cmd = ''
            if ($h.PSObject.Properties['command']) { $cmd = [string]$h.command }
            $out += [pscustomobject]@{ Command = $cmd; Matcher = $matcher }
        }
    }
    return $out
}

function Test-Settings {
    # Read-only. The installer never edits settings.json, but -Verify must say
    # whether the hooks the library depends on are actually wired.
    if (-not (Test-Path -LiteralPath $Settings -PathType Leaf)) {
        Write-Host "DRIFT: $Settings not found - hooks are not enabled (see settings.example.windows.json)"
        $script:DriftCount++
        return
    }
    $text = [System.IO.File]::ReadAllText($Settings)
    try {
        $data = $text | ConvertFrom-Json
    } catch {
        Write-Host "DRIFT: $Settings is not valid JSON ($($_.Exception.Message))"
        $script:DriftCount++
        return
    }
    if ($data.PSObject.Properties['disableAllHooks'] -and $data.disableAllHooks -eq $true) {
        Write-Host "DRIFT: disableAllHooks is true in $Settings - every hook is off"
        $script:DriftCount++
    }
    $hooks = $null
    if ($data.PSObject.Properties['hooks']) { $hooks = $data.hooks }
    $wanted = @(@('PostToolUse', 'pycheck.py'), @('SubagentStop', 'check-evidence.py'), @('PreToolUse', 'no-ask.py'), @('Stop', 'no-punt.py'), @('SubagentStop', 'ledger.py'), @('SessionStart', 'ledger.py'), @('Stop', 'ledger.py'), @('PreToolUse', 'check-spawn.py'), @('PostToolUse', 'ledger.py'), @('PermissionRequest', 'permit.py'), @('SubagentStart', 'ledger.py'), @('Stop', 'check-evidence.py'))
    foreach ($pair in $wanted) {
        $eventName = $pair[0]
        $scriptName = $pair[1]
        $found = @(Get-HookCommands -Hooks $hooks -EventName $eventName | Where-Object { $_.Command -like "*$scriptName*" })
        if ($found.Count -eq 0) {
            Write-Host "DRIFT: $eventName does not run $scriptName - merge the hooks block from settings.example.windows.json [$Settings]"
            $script:DriftCount++
            continue
        }
        foreach ($f in $found) {
            if ($f.Command -like '*ABSOLUTE/PATH/TO*') {
                Write-Host "DRIFT: $eventName $scriptName still has the placeholder interpreter path [$Settings]"
                $script:DriftCount++
            } else {
                Write-Host "OK: $eventName runs $scriptName (matcher '$($f.Matcher)') [$Settings]"
            }
        }
    }
    if ($null -ne $hooks) {
        foreach ($prop in $hooks.PSObject.Properties) {
            foreach ($c in @(Get-HookCommands -Hooks $hooks -EventName $prop.Name)) {
                if ($c.Command -match $V1HookPattern) {
                    Write-Host "DRIFT: $($prop.Name) still references a v1 hook script (retired) - remove it [$Settings]"
                    $script:DriftCount++
                }
            }
        }
    }
    # Auto mode: custom classifier rules must extend the built-ins, not replace them.
    $perms = $null
    if ($data.PSObject.Properties['permissions']) { $perms = $data.permissions }
    $auto = $null
    if ($data.PSObject.Properties['autoMode']) { $auto = $data.autoMode }
    $allowRules = $null
    if ($null -ne $auto -and $auto.PSObject.Properties['allow'] -and $null -ne $auto.allow) { $allowRules = @($auto.allow) }
    $defaultMode = ''
    if ($null -ne $perms -and $perms.PSObject.Properties['defaultMode']) { $defaultMode = [string]$perms.defaultMode }
    if ($null -ne $allowRules -and -not ($allowRules -contains '$defaults')) {
        Write-Host ('DRIFT: autoMode.allow replaces the built-in classifier rules - add "$defaults" (see settings.example.windows.json) [' + $Settings + ']')
        $script:DriftCount++
    } elseif ($null -eq $allowRules -and $defaultMode -eq 'auto') {
        Write-Host ('NOTE: auto mode without autoMode.allow - the classifier runs on its built-in rules only; template in settings.example.windows.json [' + $Settings + ']')
    }
    # Exact Bash allow rules match one command string forever; prefix rules are what reduce prompts.
    $dead = @()
    if ($null -ne $perms -and $perms.PSObject.Properties['allow'] -and $null -ne $perms.allow) {
        $dead = @(@($perms.allow) | Where-Object { ($_ -is [string]) -and $_.StartsWith('Bash(') -and -not $_.Contains('*') })
    }
    if ($dead.Count -ge 5) {
        Write-Host ('NOTE: ' + $dead.Count + ' exact Bash allow rules never match a different command - prefer Bash(cmd *); scripts/friction.py lists them [' + $Settings + ']')
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

function Test-SameContent {
    param([string]$A, [string]$B)
    if (-not (Test-Path -LiteralPath $A -PathType Leaf) -or -not (Test-Path -LiteralPath $B -PathType Leaf)) { return $false }
    try {
        return ((Get-FileHash -Algorithm SHA256 -LiteralPath $A).Hash -eq (Get-FileHash -Algorithm SHA256 -LiteralPath $B).Hash)
    } catch {
        return $false
    }
}

function Install-ManagedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (Test-Path -LiteralPath $Target) {
        if (Test-SameContent -A $Source -B $Target) {
            Write-Host "Unchanged: $Target"
            return
        }
        if ($NoOverwrite) {
            Write-Host "Skipped (exists, differs): $Target"
            $script:IncompleteCount++
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
    if (Test-SameContent -A $Source -B $Target) {
        Write-Host "OK: $Target"
    } else {
        Write-Host "DRIFT: $Target"
        $script:DriftCount++
    }
}

function Test-V1Agent {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    return ([System.IO.File]::ReadAllText($Path).Contains($V1AgentSignature))
}

function Retire-Path {
    param([Parameter(Mandatory = $true)][string]$Target)
    if (-not (Test-Path -LiteralPath $Target)) { return }
    if ($NoOverwrite) {
        Write-Host "WARNING: v1 file still installed: $Target (-NoOverwrite: leaving as is)"
        $script:IncompleteCount++
        return
    }
    $moved = "$Target.retired.$Timestamp"
    Write-Host "Retiring v1 file: $Target -> $moved"
    Invoke-Step -Description "Move-Item '$Target' '$moved'" -Script { Move-Item -LiteralPath $Target -Destination $moved -Force }
}

function Retire-Agent {
    param([Parameter(Mandatory = $true)][string]$Target)
    if (-not (Test-Path -LiteralPath $Target)) { return }
    if (Test-V1Agent -Path $Target) {
        Retire-Path -Target $Target
    } else {
        Write-Host "NOTE: $Target has a v1 role name but is not a v1 file - left in place (your own agent?)"
    }
}

function Test-Retired {
    param([Parameter(Mandatory = $true)][string]$Target)
    if (Test-Path -LiteralPath $Target) {
        Write-Host "STALE: $Target (v1 file still installed; run the installer to retire it)"
        $script:DriftCount++
    }
}

function Test-RetiredAgent {
    param([Parameter(Mandatory = $true)][string]$Target)
    if (Test-V1Agent -Path $Target) {
        Test-Retired -Target $Target
    } elseif (Test-Path -LiteralPath $Target) {
        Write-Host "NOTE: $Target is not a v1 file; not managed by this installer"
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
    if ($Verify) { Test-RetiredAgent -Target (Join-Path $DestAgents "$name.md") }
    else { Retire-Agent -Target (Join-Path $DestAgents "$name.md") }
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
        Write-Host 'Verify result: OK (files match, import present, hooks wired).'
        exit 0
    }
    Write-Host "Verify result: DRIFT detected in $DriftCount item(s)."
    exit 1
}

Set-LibraryImport

Write-Host ''
if ($IncompleteCount -gt 0) {
    Write-Host "Install INCOMPLETE: $IncompleteCount item(s) skipped under -NoOverwrite (see WARNING/Skipped lines above)."
    Write-Host 'Re-run without -NoOverwrite, or resolve them by hand; then .\install.ps1 -Verify.'
    exit 3
}
Write-Host "Agents installed in:          $DestAgents  (explore, implement, review)"
Write-Host "Skills installed in:          $DestSkills  (/lost-mary, /validate, /pr, /ledger)"
Write-Host "Hook scripts + rules in:      $DestLib"
Write-Host "Operating rules imported by:  $ClaudeMd"
Write-Host ''
Write-Host 'Hooks are NOT installed automatically (they live in settings.json, which this'
Write-Host "installer never edits). Merge the 'hooks' block from settings.example.windows.json"
Write-Host 'into ~/.claude/settings.json (use an absolute interpreter path: `python` on PATH is'
Write-Host 'often the Microsoft Store stub); a running Claude Code normally picks it up live.'
Write-Host 'Then run .\install.ps1 -Verify - it fails until the hooks are wired.'
