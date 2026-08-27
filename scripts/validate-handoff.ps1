#!/usr/bin/env pwsh
# Thin wrapper for Windows PowerShell / PowerShell Core.
# Usage:
#   ./scripts/validate-handoff.ps1 path\to\handoff.json
#   Get-Content handoff.json -Raw | ./scripts/validate-handoff.ps1 -

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot

if ($args.Count -ne 1 -or $args[0] -in @('-h', '--help')) {
    Write-Error 'Usage: validate-handoff.ps1 <file.json|->'
    exit 2
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Error 'Python is required but was not found in PATH.'
    exit 2
}

& $python.Source "$root/scripts/validate-handoff.py" $args[0]
exit $LASTEXITCODE
