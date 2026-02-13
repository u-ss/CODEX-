param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..")
Set-Location $repoRoot

if ($DryRun) {
    powershell -ExecutionPolicy Bypass -File tools/clean_runtime_artifacts.ps1 -DryRun
} else {
    powershell -ExecutionPolicy Bypass -File tools/clean_runtime_artifacts.ps1
}

python tools/repo_hygiene_check.py
