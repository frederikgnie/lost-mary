# Stage a plugin-only copy of this repository for `claude plugin eval`.
#
# Why this exists: the eval harness refuses a plugin in which any file has more
# than one name ("a file in the plugin has more than one name (a hard link) -
# case definitions must not be reachable by another name"). It scans the whole
# plugin root and does not honour .gitignore, so a dev `.venv` created by uv -
# which hard-links 475 files out of its cache here - blocks every run from the
# repository root, even though `.venv` is ignored and is not plugin content.
#
# Measured 2026-09-15 on Claude Code 2.1.272: `claude plugin eval .` aborts
# before spending anything; the same suite against a staged copy runs fine.
#
# Usage:
#   ./evals/stage.ps1                       # stage, print the path
#   claude plugin eval <path> --runs 1 --no-publish
#
# Results land under <staged>/evals/results/, not in the repository.

[CmdletBinding()]
param(
    [string] $Destination = (Join-Path ([System.IO.Path]::GetTempPath()) "lost-mary-plugin")
)

$ErrorActionPreference = "Stop"

# Plugin content only: the manifest, the roles, the procedures, the hook scripts
# those hooks point at, and the suite. Everything else in the repository - the
# venv, the tests, CI, docs - is not part of what is being evaluated.
$Parts = @(".claude-plugin", "agents", "skills", "hooks", "scripts", "evals")

$RepoRoot = Split-Path -Parent $PSScriptRoot

if (Test-Path -LiteralPath $Destination) {
    Remove-Item -LiteralPath $Destination -Recurse -Force
}
New-Item -ItemType Directory -Path $Destination | Out-Null

foreach ($part in $Parts) {
    $source = Join-Path $RepoRoot $part
    if (-not (Test-Path -LiteralPath $source)) {
        throw "missing plugin part: $source"
    }
    Copy-Item -LiteralPath $source -Destination $Destination -Recurse -Force
}

# Results are run artefacts; a staged copy starts without the previous readings.
$staleResults = Join-Path $Destination "evals\results"
if (Test-Path -LiteralPath $staleResults) {
    Remove-Item -LiteralPath $staleResults -Recurse -Force
}

# The check the harness itself makes. Fail here, with the offending paths, rather
# than inside a run that reports it as a scored case failure.
$linked = Get-ChildItem -LiteralPath $Destination -Recurse -File |
    Where-Object { $_.LinkType -eq "HardLink" }
if ($linked) {
    $linked | ForEach-Object { Write-Error "hard link in staged copy: $($_.FullName)" }
    throw "staged copy contains hard links; the eval harness will refuse it"
}

Write-Output $Destination
