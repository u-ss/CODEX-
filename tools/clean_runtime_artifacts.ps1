param(
    [switch]$DryRun,
    [switch]$IncludeDesktopWorkflowLogs,
    [switch]$IncludeGoldenProfile
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$targets = @(
    "_temp",
    "_screenshots",
    "_logs",
    "_outputs"
)

if ($IncludeDesktopWorkflowLogs) {
    $targets += ".agent/workflows/desktop/logs"
}

if ($IncludeGoldenProfile) {
    $targets += ".agent/golden_profile"
}

$cachePatterns = @("__pycache__", "*.pyc")

Write-Host "[info] repo root: $root"
Write-Host "[info] include_desktop_logs=$IncludeDesktopWorkflowLogs include_golden_profile=$IncludeGoldenProfile"

foreach ($t in $targets) {
    if (Test-Path $t) {
        if ($DryRun) {
            Write-Host "[dry-run] remove dir: $t"
        } else {
            Remove-Item -Recurse -Force $t
            Write-Host "[ok] removed dir: $t"
        }
    }
}

$cacheDirs = Get-ChildItem -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.FullName -notlike "*\.venv\*" -and
        $_.FullName -notlike "*\node_modules\*" -and
        $_.FullName -notlike "*\.git\*" -and
        $_.FullName -notlike "*\.agent\workflows\claude-skills\*"
    }
foreach ($d in $cacheDirs) {
    if ($DryRun) {
        Write-Host "[dry-run] remove cache dir: $($d.FullName)"
    } else {
        Remove-Item -Recurse -Force $d.FullName
        Write-Host "[ok] removed cache dir: $($d.FullName)"
    }
}

$pycFiles = Get-ChildItem -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.FullName -notlike "*\.venv\*" -and
        $_.FullName -notlike "*\node_modules\*" -and
        $_.FullName -notlike "*\.git\*" -and
        $_.FullName -notlike "*\.agent\workflows\claude-skills\*"
    }
foreach ($f in $pycFiles) {
    if ($DryRun) {
        Write-Host "[dry-run] remove file: $($f.FullName)"
    } else {
        Remove-Item -Force $f.FullName
        Write-Host "[ok] removed file: $($f.FullName)"
    }
}

Write-Host "[done] runtime cleanup complete"
