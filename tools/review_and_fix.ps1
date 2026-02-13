param(
    [string]$Base = "main",
    [int]$ChunkSize = 8,
    [int]$SplitThreshold = 8,
    [int]$ReviewRetryCount = 2,
    [int]$FixRetryCount = 1,
    [int]$FixRounds = 1,
    [switch]$Uncommitted,
    [switch]$DryRun,
    [switch]$ReviewOnly,
    [switch]$SkipFinalVerification,
    [bool]$UseFullAuto = $false,
    [string]$CodexModel = "",
    [string]$OutputDir = "",
    [string]$ExtraReviewInstructions = "",
    [string]$ExtraFixInstructions = "",
    [int]$MaxReviewCharsPerChunk = 12000,
    [string]$ReviewModel = "",
    [string]$ReasoningEffort = "",
    [int]$CodexTimeoutSeconds = 300,
    [int]$HealthCheckTimeoutSeconds = 45,
    [int]$FailFastOnCodexTimeout = 1,
    [switch]$SkipCodexHealthCheck,
    [string[]]$ExcludeGlobs = @(
        "_outputs/*",
        "_logs/*",
        "_temp/*",
        "_screenshots/*",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.webp",
        "*.gif",
        "*.mp4",
        "*.mp3",
        "*.wav",
        "*.blend"
    )
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-RepoRoot {
    return Split-Path -Parent $PSScriptRoot
}

function Test-CommandExists {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function New-Timestamp {
    return Get-Date -Format "yyyyMMdd_HHmmss"
}

function Get-PowerShellExecutable {
    $command = Get-Command powershell -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $fallback = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (Test-Path $fallback) {
        return $fallback
    }
    throw "powershell executable not found"
}

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Text = ""
    )
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Read-TaskResultText {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [int]$TimeoutMs = 10000
    )
    try {
        if ($Task.Wait($TimeoutMs)) {
            return [string]$Task.GetAwaiter().GetResult()
        }
    }
    catch {
    }
    return ""
}

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process
    )
    try {
        $Process.Kill()
    }
    catch {
    }
    try {
        & taskkill /PID $Process.Id /T /F *> $null
    }
    catch {
    }
}

function Get-CodexCliEntrypoint {
    $codexCommand = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codexCommand) {
        throw "codex command not found"
    }

    $basedir = Split-Path -Parent $codexCommand.Source
    $codexJs = Join-Path $basedir "node_modules/@openai/codex/bin/codex.js"
    if (-not (Test-Path $codexJs)) {
        throw "codex CLI entrypoint not found: $codexJs"
    }

    $nodeFromBasedir = Join-Path $basedir "node.exe"
    $nodeCommand = if (Test-Path $nodeFromBasedir) {
        $nodeFromBasedir
    }
    else {
        $node = Get-Command node -ErrorAction SilentlyContinue
        if (-not $node) {
            throw "node command not found (required by codex CLI)"
        }
        $node.Source
    }

    return @{
        nodePath = $nodeCommand
        codexJs  = $codexJs
    }
}

function Write-CombinedOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Mode,
        [Parameter(Mandatory = $true)][int]$Attempt,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][bool]$TimedOut,
        [AllowEmptyString()][string]$StdoutText = "",
        [AllowEmptyString()][string]$StderrText = ""
    )
    $body = @"
[meta]
mode=$Mode
attempt=$Attempt
exit_code=$ExitCode
timed_out=$TimedOut

[stdout]
$StdoutText

[stderr]
$StderrText
"@
    Write-Utf8NoBom -Path $Path -Text $body
}

function Read-TextIfExists {
    param([string]$Path)
    if (-not $Path) {
        return ""
    }
    if (-not (Test-Path $Path)) {
        return ""
    }
    return Get-Content -Raw -Encoding utf8 $Path
}

function Extract-ReviewResultBlock {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return ""
    }
    # 閉じタグのバリエーション対応: <</REVIEW_RESULT>> と </REVIEW_RESULT> の両方にマッチ
    $m = [regex]::Match($Text, '(?s)<<REVIEW_RESULT>>\s*(.*?)\s*<{1,2}/REVIEW_RESULT>{1,2}')
    if ($m.Success) {
        return [string]$m.Groups[1].Value.Trim()
    }
    return ""
}

function Get-ChunkReviewBundle {
    param([Parameter(Mandatory = $true)]$Chunk)

    $stdoutPath = [string]$Chunk.result_path
    $stderrPath = [string]$Chunk.stderr_path
    if ([string]::IsNullOrWhiteSpace($stderrPath) -and $stdoutPath -match '\.txt$') {
        $stderrPath = $stdoutPath -replace '\.txt$', '.stderr.txt'
    }
    $combinedPath = [string]$Chunk.combined_path
    if ([string]::IsNullOrWhiteSpace($combinedPath) -and $stdoutPath -match '\.txt$') {
        $combinedPath = $stdoutPath -replace '\.txt$', '.combined.txt'
    }

    $stdoutText = Read-TextIfExists -Path $stdoutPath
    $stderrText = Read-TextIfExists -Path $stderrPath
    $combinedText = Read-TextIfExists -Path $combinedPath

    if ([string]::IsNullOrWhiteSpace($combinedText)) {
        if (-not [string]::IsNullOrWhiteSpace($stdoutText) -or -not [string]::IsNullOrWhiteSpace($stderrText)) {
            $combinedText = "[stdout]`n$stdoutText`n`n[stderr]`n$stderrText"
        }
    }

    $structured = ""
    if (-not [string]::IsNullOrWhiteSpace($combinedText)) {
        $structured = Extract-ReviewResultBlock -Text $combinedText
    }
    if ([string]::IsNullOrWhiteSpace($structured) -and -not [string]::IsNullOrWhiteSpace($stdoutText)) {
        $structured = Extract-ReviewResultBlock -Text $stdoutText
    }
    if ([string]::IsNullOrWhiteSpace($structured) -and -not [string]::IsNullOrWhiteSpace($stderrText)) {
        $structured = Extract-ReviewResultBlock -Text $stderrText
    }

    $effective = if (-not [string]::IsNullOrWhiteSpace($structured)) { $structured }
    elseif (-not [string]::IsNullOrWhiteSpace($combinedText)) { $combinedText }
    elseif (-not [string]::IsNullOrWhiteSpace($stdoutText)) { $stdoutText }
    else { $stderrText }

    return [pscustomobject]@{
        stdout_path     = $stdoutPath
        stderr_path     = $stderrPath
        combined_path   = $combinedPath
        stdout_text     = $stdoutText
        stderr_text     = $stderrText
        combined_text   = $combinedText
        structured_text = $structured
        effective_text  = $effective
    }
}

function Invoke-ReviewScript {
    param(
        [Parameter(Mandatory = $true)][string]$PowerShellExe,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][bool]$UseUncommittedMode,
        [Parameter(Mandatory = $true)][bool]$DoDryRun,
        [Parameter(Mandatory = $true)][string]$BaseRef,
        [Parameter(Mandatory = $true)][int]$ChunkSizeValue,
        [Parameter(Mandatory = $true)][int]$SplitThresholdValue,
        [Parameter(Mandatory = $true)][int]$RetryCountValue,
        [Parameter(Mandatory = $true)][int]$CodexTimeoutSecondsValue,
        [Parameter(Mandatory = $true)][int]$HealthCheckTimeoutSecondsValue,
        [Parameter(Mandatory = $true)][int]$FailFastOnCodexTimeoutValue,
        [Parameter(Mandatory = $true)][bool]$SkipCodexHealthCheckValue,
        [AllowEmptyString()][string]$ExtraInstructionsValue = "",
        [Parameter(Mandatory = $true)][string[]]$ExcludeGlobsValue,
        [AllowEmptyString()][string]$ReviewModelValue = "",
        [AllowEmptyString()][string]$ReasoningEffortValue = ""
    )
    # $args はPowerShell自動変数のため $cmdArgs を使用
    $cmdArgs = @(
        "-ExecutionPolicy", "Bypass",
        "-File", $ScriptPath,
        "-ChunkSize", $ChunkSizeValue,
        "-SplitThreshold", $SplitThresholdValue,
        "-RetryCount", $RetryCountValue,
        "-CodexTimeoutSeconds", $CodexTimeoutSecondsValue,
        "-HealthCheckTimeoutSeconds", $HealthCheckTimeoutSecondsValue,
        "-FailFastOnCodexTimeout", $FailFastOnCodexTimeoutValue,
        "-OutputDir", $OutputPath
    )
    if ($SkipCodexHealthCheckValue) {
        $cmdArgs += "-SkipCodexHealthCheck"
    }
    if (-not [string]::IsNullOrWhiteSpace($ExtraInstructionsValue)) {
        $cmdArgs += @("-ExtraInstructions", $ExtraInstructionsValue)
    }
    if ($UseUncommittedMode) {
        $cmdArgs += "-Uncommitted"
    }
    else {
        $cmdArgs += @("-Base", $BaseRef)
    }
    if ($DoDryRun) {
        $cmdArgs += "-DryRun"
    }
    if ($ExcludeGlobsValue.Count -gt 0) {
        # -File モードでは配列引数を直接渡せないため、カンマ区切り文字列にシリアライズ
        $globsCsv = $ExcludeGlobsValue -join ","
        $cmdArgs += @("-ExcludeGlobs", $globsCsv)
    }
    if (-not [string]::IsNullOrWhiteSpace($ReviewModelValue)) {
        $cmdArgs += @("-ReviewModel", $ReviewModelValue)
    }
    if (-not [string]::IsNullOrWhiteSpace($ReasoningEffortValue)) {
        $cmdArgs += @("-ReasoningEffort", $ReasoningEffortValue)
    }

    # stderr警告（git CRLF等）が$ErrorActionPreference="Stop"で偽陽性エラーになるのを防止
    $prevEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $PowerShellExe @cmdArgs
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Review script failed: $ScriptPath"
    }
}

function Read-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path $Path)) {
        throw "JSON file not found: $Path"
    }
    return (Get-Content -Raw -Encoding utf8 $Path | ConvertFrom-Json)
}

function Get-ChunkFindings {
    param([Parameter(Mandatory = $true)]$Manifest)
    $chunks = @()
    foreach ($chunk in @($Manifest.chunks)) {
        $status = [string]$chunk.status
        $timedOut = [bool]$chunk.timed_out
        $codexExitCode = $null
        if ($null -ne $chunk.codex_exitcode) {
            $codexExitCode = [int]$chunk.codex_exitcode
        }
        $bundle = Get-ChunkReviewBundle -Chunk $chunk
        $content = [string]$bundle.effective_text
        $state = [ordered]@{
            id               = [string]$chunk.id
            status           = $status
            files_path       = [string]$chunk.files_path
            result_path      = [string]$bundle.stdout_path
            stderr_path      = [string]$bundle.stderr_path
            combined_path    = [string]$bundle.combined_path
            has_findings     = $null
            reason           = ""
            review_text_path = if (-not [string]::IsNullOrWhiteSpace($bundle.combined_path) -and -not [string]::IsNullOrWhiteSpace($bundle.combined_text)) {
                [string]$bundle.combined_path
            }
            elseif (-not [string]::IsNullOrWhiteSpace($bundle.stdout_path) -and -not [string]::IsNullOrWhiteSpace($bundle.stdout_text)) {
                [string]$bundle.stdout_path
            }
            else {
                [string]$bundle.stderr_path
            }
        }

        if ($status -eq "DRY_RUN") {
            $state.has_findings = $null
            $state.reason = "dry_run"
        }
        elseif ($status -eq "SKIPPED_EMPTY_PATCH") {
            $state.has_findings = $false
            $state.reason = "empty_patch"
        }
        elseif ($status -eq "SKIPPED_CIRCUIT_OPEN") {
            $state.has_findings = $null
            $state.reason = "review_transport_error"
        }
        elseif ([string]::IsNullOrWhiteSpace($content)) {
            $state.has_findings = $null
            $state.reason = "empty_review_output"
        }
        elseif ($timedOut -or $codexExitCode -eq 124) {
            $state.has_findings = $null
            $state.reason = "review_execution_timeout"
        }
        elseif ($content -match '(?im)(^|\n)\s*(TIMEOUT:|CHUNK_RUNTIME_ERROR:|EXCEPTION:|EMPTY_OUTPUT:)\s*') {
            $state.has_findings = $null
            $state.reason = "review_execution_error"
        }
        elseif ($content -match '(?im)(^|\n)\s*(No findings in scoped files\.?|No findings\.?|No issues found\.?|\u554f\u984c\u306a\u3057|\u6307\u6458\u306a\u3057|LGTM)\s*($|\n)') {
            $state.has_findings = $false
            $state.reason = "no_findings_phrase"
        }
        elseif ($status -ne "OK") {
            if ($content -match '(?im)(not\s+logged\s+in|authentication\s+failed|invalid\s+api\s+key|rate\s*limit|too\s+many\s+requests|connection\s+refused|dns|tls\s+handshake|econn)') {
                $state.has_findings = $null
                $state.reason = "review_transport_error"
            }
            else {
                $state.has_findings = $null
                $state.reason = "review_failed"
            }
        }
        else {
            $state.has_findings = $true
            $state.reason = "findings_present"
        }

        $chunks += [pscustomobject]$state
    }
    return $chunks
}

function Read-FileLines {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path $Path)) {
        return @()
    }
    return @(Get-Content -Encoding utf8 $Path | Where-Object { $_ -and $_.Trim().Length -gt 0 })
}

function Get-ChangedFilesSnapshot {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $unstaged = @(& git -C $RepoRoot -c core.quotepath=false diff --name-only 2>$null)
    $staged = @(& git -C $RepoRoot -c core.quotepath=false diff --cached --name-only 2>$null)
    $untracked = @(& git -C $RepoRoot -c core.quotepath=false ls-files --others --exclude-standard 2>$null)
    return @($unstaged + $staged + $untracked | Where-Object { $_ -and $_.Trim().Length -gt 0 } | Sort-Object -Unique)
}

function Get-FileFingerprint {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    }
    if (Test-Path -LiteralPath $Path -PathType Container) {
        return "__DIR__"
    }
    return "__MISSING__"
}

function Get-GitPathStatusToken {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PathValue
    )
    $line = @(& git -C $RepoRoot status --porcelain=1 -- $PathValue 2>$null | Select-Object -First 1)
    if ($line.Count -eq 0 -or [string]::IsNullOrWhiteSpace([string]$line[0])) {
        return "__CLEAN__"
    }
    $text = [string]$line[0]
    if ($text.Length -ge 2) {
        return $text.Substring(0, 2)
    }
    return $text
}

function New-ScopeGuardSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string[]]$AllowedFiles,
        [Parameter(Mandatory = $true)][string]$SnapshotRoot
    )
    $allowedSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($f in @($AllowedFiles)) {
        if (-not [string]::IsNullOrWhiteSpace($f)) {
            [void]$allowedSet.Add($f.Trim())
        }
    }

    $baselineChanged = @(Get-ChangedFilesSnapshot -RepoRoot $RepoRoot)
    $baselineChangedSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($f in $baselineChanged) {
        [void]$baselineChangedSet.Add($f)
    }

    $baselineOutOfScope = @($baselineChanged | Where-Object { -not $allowedSet.Contains($_) })
    $backupRoot = Join-Path $SnapshotRoot "baseline_backup"
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

    $baselineFingerprints = @{}
    $baselineStatusTokens = @{}
    foreach ($relPath in $baselineOutOfScope) {
        $fullPath = Join-Path $RepoRoot $relPath
        $baselineFingerprints[$relPath] = Get-FileFingerprint -Path $fullPath
        $baselineStatusTokens[$relPath] = Get-GitPathStatusToken -RepoRoot $RepoRoot -PathValue $relPath
        if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
            $backupPath = Join-Path $backupRoot $relPath
            $backupParent = Split-Path -Parent $backupPath
            if ($backupParent -and -not (Test-Path -LiteralPath $backupParent)) {
                New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
            }
            Copy-Item -LiteralPath $fullPath -Destination $backupPath -Force
        }
    }

    return [pscustomobject]@{
        allowed_set            = $allowedSet
        baseline_changed_set   = $baselineChangedSet
        baseline_out_of_scope  = @($baselineOutOfScope)
        baseline_fingerprints  = $baselineFingerprints
        baseline_status_tokens = $baselineStatusTokens
        backup_root            = $backupRoot
    }
}

function Test-ScopeGuardViolations {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)]$Snapshot
    )
    $postChanged = @(Get-ChangedFilesSnapshot -RepoRoot $RepoRoot)

    $newOutOfScope = @()
    foreach ($relPath in $postChanged) {
        if ($Snapshot.allowed_set.Contains($relPath)) {
            continue
        }
        if ($Snapshot.baseline_changed_set.Contains($relPath)) {
            continue
        }
        $newOutOfScope += $relPath
    }

    $mutatedExistingOutOfScope = @()
    foreach ($relPath in @($Snapshot.baseline_out_of_scope)) {
        $fullPath = Join-Path $RepoRoot $relPath
        $before = if ($Snapshot.baseline_fingerprints.ContainsKey($relPath)) {
            [string]$Snapshot.baseline_fingerprints[$relPath]
        }
        else {
            "__UNKNOWN__"
        }
        $after = Get-FileFingerprint -Path $fullPath
        $beforeStatus = if ($Snapshot.baseline_status_tokens.ContainsKey($relPath)) {
            [string]$Snapshot.baseline_status_tokens[$relPath]
        }
        else {
            "__UNKNOWN__"
        }
        $afterStatus = Get-GitPathStatusToken -RepoRoot $RepoRoot -PathValue $relPath
        if ($before -ne $after -or $beforeStatus -ne $afterStatus) {
            $mutatedExistingOutOfScope += $relPath
        }
    }

    $allOutOfScope = @($newOutOfScope + $mutatedExistingOutOfScope | Sort-Object -Unique)
    return [pscustomobject]@{
        new_out_of_scope              = @($newOutOfScope | Sort-Object -Unique)
        mutated_existing_out_of_scope = @($mutatedExistingOutOfScope | Sort-Object -Unique)
        all_out_of_scope              = $allOutOfScope
    }
}

function Test-GitPathTracked {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PathValue
    )
    $prevEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & git -C $RepoRoot ls-files --error-unmatch -- $PathValue *> $null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
}

function Test-GitPathExistsInHead {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PathValue
    )
    $prevEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & git -C $RepoRoot cat-file -e ("HEAD:{0}" -f $PathValue) *> $null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
}

function Restore-ScopeGuardViolations {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)]$Snapshot,
        [Parameter(Mandatory = $true)][string[]]$Paths
    )
    foreach ($relPath in @($Paths | Sort-Object -Unique)) {
        if ([string]::IsNullOrWhiteSpace($relPath)) {
            continue
        }

        $fullPath = Join-Path $RepoRoot $relPath
        $hasBaseline = $Snapshot.baseline_fingerprints.ContainsKey($relPath)
        if ($hasBaseline) {
            $before = [string]$Snapshot.baseline_fingerprints[$relPath]
            if ($before -eq "__MISSING__") {
                if (Test-Path -LiteralPath $fullPath) {
                    Remove-Item -LiteralPath $fullPath -Recurse -Force -ErrorAction SilentlyContinue
                }
                continue
            }
            if ($before -eq "__DIR__") {
                if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
                    New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
                }
                continue
            }

            $backupPath = Join-Path $Snapshot.backup_root $relPath
            if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
                $parent = Split-Path -Parent $fullPath
                if ($parent -and -not (Test-Path -LiteralPath $parent)) {
                    New-Item -ItemType Directory -Path $parent -Force | Out-Null
                }
                Copy-Item -LiteralPath $backupPath -Destination $fullPath -Force
                continue
            }
        }

        $tracked = Test-GitPathTracked -RepoRoot $RepoRoot -PathValue $relPath
        if ($tracked) {
            $prevEap = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                & git -C $RepoRoot restore --source=HEAD --worktree --staged -- $relPath *> $null
                if ($LASTEXITCODE -ne 0) {
                    & git -C $RepoRoot checkout -- $relPath *> $null
                }
            }
            finally {
                $ErrorActionPreference = $prevEap
            }
            if (-not (Test-GitPathExistsInHead -RepoRoot $RepoRoot -PathValue $relPath)) {
                $prevEap = $ErrorActionPreference
                try {
                    $ErrorActionPreference = "Continue"
                    & git -C $RepoRoot rm --cached --force -- $relPath *> $null
                }
                finally {
                    $ErrorActionPreference = $prevEap
                }
                if (Test-Path -LiteralPath $fullPath) {
                    Remove-Item -LiteralPath $fullPath -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
        }
        else {
            if (Test-Path -LiteralPath $fullPath) {
                Remove-Item -LiteralPath $fullPath -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Build-FixPrompt {
    param(
        [Parameter(Mandatory = $true)][string[]]$Files,
        [Parameter(Mandatory = $true)][string]$ReviewText,
        [Parameter(Mandatory = $true)][string]$ScopeText,
        [Parameter(Mandatory = $true)][string]$ChunkId,
        [Parameter(Mandatory = $true)][int]$Round,
        [Parameter(Mandatory = $true)][string]$ExtraInstructions,
        [Parameter(Mandatory = $true)][int]$MaxChars
    )
    $filesBlock = if ($Files.Count -gt 0) {
        ($Files | ForEach-Object { '- `{0}`' -f $_ }) -join "`n"
    }
    else {
        "- (no files listed)"
    }

    $normalizedReview = if ([string]::IsNullOrWhiteSpace($ReviewText)) {
        "[review output is empty]"
    }
    else {
        $ReviewText
    }

    if ($normalizedReview.Length -gt $MaxChars) {
        $normalizedReview = $normalizedReview.Substring(0, $MaxChars) + "`n[review output truncated by wrapper]"
    }

    $extraBlock = if ([string]::IsNullOrWhiteSpace($ExtraInstructions)) {
        ""
    }
    else {
        "`nAdditional instructions:`n$ExtraInstructions`n"
    }

    $prompt = @"
Fix the issues reported in the review findings for chunk $ChunkId (round $Round).
Scope: $ScopeText

Constraints:
1. Edit only files listed in "Files in scope".
2. Prioritize ERROR and CAUTION fixes.
3. ADVISORY fixes are optional and should be applied only if low-risk.
4. Keep changes minimal and consistent with the repository style.

Files in scope:
$filesBlock

Review findings:
$normalizedReview

Response format:
- Fixed: bullet list
- Remaining: bullet list (if any)
- Verification: commands run (or "not run")
$extraBlock
"@
    return $prompt
}

function Invoke-CodexExecFix {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$Model,
        [Parameter(Mandatory = $true)][bool]$EnableFullAuto,
        [Parameter(Mandatory = $true)][int]$Retries,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)]$CodexEntrypoint,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath,
        [Parameter(Mandatory = $true)][string]$CombinedPath
    )
    $attempt = 0
    $lastTimedOut = $false
    while ($attempt -le $Retries) {
        $attempt++
        $nodePath = [string]$CodexEntrypoint.nodePath
        $codexJs = [string]$CodexEntrypoint.codexJs

        $argsList = @($codexJs, "exec")
        if (-not [string]::IsNullOrWhiteSpace($Model)) {
            $argsList += @("-m", $Model)
        }
        if ($EnableFullAuto) {
            $argsList += "--full-auto"
        }
        $argsList += "-"

        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $nodePath
        $psi.Arguments = ($argsList | ForEach-Object {
                $a = [string]$_
                if ($a -match '[\s"]') {
                    $escaped = $a -replace '"', '\"'
                    '"{0}"' -f $escaped
                }
                else { $a }
            }) -join " "
        $psi.WorkingDirectory = $WorkingDirectory
        $psi.UseShellExecute = $false
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi

        try {
            $null = $proc.Start()
            $proc.StandardInput.Write($Prompt)
            $proc.StandardInput.Close()

            $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
            $stderrTask = $proc.StandardError.ReadToEndAsync()
            $timeoutMs = $TimeoutSeconds * 1000
            $exited = $proc.WaitForExit($timeoutMs)
            $timedOut = $false
            if (-not $exited) {
                Write-Host "[warn] codex fix process timed out after ${TimeoutSeconds}s, killing..."
                Stop-ProcessTree -Process $proc
                try { $proc.WaitForExit(5000) } catch {}
                $timedOut = $true
            }

            $stdoutText = Read-TaskResultText -Task $stdoutTask -TimeoutMs 10000
            $stderrText = Read-TaskResultText -Task $stderrTask -TimeoutMs 10000

            if ($timedOut) {
                if ([string]::IsNullOrWhiteSpace($stdoutText)) {
                    $stdoutText = "TIMEOUT: process killed after ${TimeoutSeconds}s"
                }
                if ([string]::IsNullOrWhiteSpace($stderrText)) {
                    $stderrText = "TIMEOUT: process killed after ${TimeoutSeconds}s"
                }
            }

            Write-Utf8NoBom -Path $StdoutPath -Text $stdoutText
            Write-Utf8NoBom -Path $StderrPath -Text $stderrText
            $exitCode = if ($timedOut) { 124 } else { $proc.ExitCode }
            Write-CombinedOutput `
                -Path $CombinedPath `
                -Mode "exec_fix" `
                -Attempt $attempt `
                -ExitCode $exitCode `
                -TimedOut:$timedOut `
                -StdoutText $stdoutText `
                -StderrText $stderrText

            if ($exitCode -eq 0 -and [string]::IsNullOrWhiteSpace($stdoutText) -and [string]::IsNullOrWhiteSpace($stderrText)) {
                $exitCode = 125
                Write-Utf8NoBom -Path $StderrPath -Text "EMPTY_OUTPUT: codex fix returned success but both stdout/stderr were empty."
                Write-CombinedOutput `
                    -Path $CombinedPath `
                    -Mode "exec_fix" `
                    -Attempt $attempt `
                    -ExitCode $exitCode `
                    -TimedOut:$false `
                    -StdoutText "" `
                    -StderrText "EMPTY_OUTPUT: codex fix returned success but both stdout/stderr were empty."
            }
            $lastTimedOut = $timedOut
        }
        catch {
            $exitCode = 1
            Write-Utf8NoBom -Path $StdoutPath -Text ""
            Write-Utf8NoBom -Path $StderrPath -Text ("EXCEPTION: " + $_.Exception.Message)
            Write-CombinedOutput `
                -Path $CombinedPath `
                -Mode "exec_fix" `
                -Attempt $attempt `
                -ExitCode $exitCode `
                -TimedOut:$false `
                -StdoutText "" `
                -StderrText ("EXCEPTION: " + $_.Exception.Message)
            $lastTimedOut = $false
        }
        finally {
            $proc.Dispose()
        }

        if ($exitCode -eq 0) {
            return @{
                ok       = $true
                attempt  = $attempt
                exitCode = $exitCode
                timedOut = $false
            }
        }

        if ($attempt -le $Retries) {
            Start-Sleep -Seconds 2
        }
    }

    return @{
        ok       = $false
        attempt  = $attempt
        exitCode = $exitCode
        timedOut = $lastTimedOut
    }
}

if ($FixRounds -lt 1) {
    throw "FixRounds must be >= 1"
}
if ($ChunkSize -lt 1) {
    throw "ChunkSize must be >= 1"
}
if ($SplitThreshold -lt 1) {
    throw "SplitThreshold must be >= 1"
}
if ($MaxReviewCharsPerChunk -lt 1000) {
    throw "MaxReviewCharsPerChunk must be >= 1000"
}
if ($CodexTimeoutSeconds -lt 30) {
    throw "CodexTimeoutSeconds must be >= 30"
}
if ($HealthCheckTimeoutSeconds -lt 10) {
    throw "HealthCheckTimeoutSeconds must be >= 10"
}

$repoRoot = Get-RepoRoot
Set-Location $repoRoot

if (-not (Test-CommandExists -Name "git")) {
    throw "git command not found"
}
if (-not $DryRun -and -not (Test-CommandExists -Name "codex")) {
    throw "codex command not found"
}
$powerShellExe = Get-PowerShellExecutable
$codexEntrypoint = $null
if (-not $DryRun) {
    $codexEntrypoint = Get-CodexCliEntrypoint
}
$skipCodexHealthCheckBool = [bool]$SkipCodexHealthCheck

$reviewScriptPath = Join-Path $repoRoot "tools/codex_cli_diff_review.ps1"
if (-not (Test-Path $reviewScriptPath)) {
    throw "Review script not found: $reviewScriptPath"
}

$currentBranch = (& git branch --show-current).Trim()
if (-not $Uncommitted) {
    & git rev-parse --verify $Base *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Base ref '$Base' not found"
    }
}

$timestamp = New-Timestamp
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $repoRoot "_outputs/review/codex_cli/review_and_fix/$timestamp"
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$scopeText = if ($Uncommitted) { "uncommitted changes" } else { "diff against base '$Base'" }

Write-Host "[info] repo root: $repoRoot"
Write-Host "[info] output dir: $OutputDir"
Write-Host "[info] branch: $currentBranch"
Write-Host "[info] scope: $scopeText"
Write-Host "[info] mode: dry_run=$([bool]$DryRun) review_only=$([bool]$ReviewOnly)"

$runManifest = [ordered]@{
    generated_at                = (Get-Date).ToString("o")
    repo_root                   = $repoRoot
    branch                      = $currentBranch
    scope                       = $scopeText
    base                        = if ($Uncommitted) { $null } else { $Base }
    uncommitted                 = [bool]$Uncommitted
    dry_run                     = [bool]$DryRun
    review_only                 = [bool]$ReviewOnly
    fix_rounds_requested        = $FixRounds
    review_retry_count          = $ReviewRetryCount
    fix_retry_count             = $FixRetryCount
    codex_timeout_seconds       = $CodexTimeoutSeconds
    healthcheck_timeout_seconds = $HealthCheckTimeoutSeconds
    fail_fast_on_codex_timeout  = [bool]$FailFastOnCodexTimeout
    skip_codex_healthcheck      = [bool]$SkipCodexHealthCheck
    chunk_size                  = $ChunkSize
    split_threshold             = $SplitThreshold
    final_verification_skipped  = [bool]$SkipFinalVerification
    rounds                      = @()
    final_verification          = $null
    no_findings_reached         = $false
    unknown_results             = $false
}

$appliedFixes = $false
$stoppedByNoFindings = $false

for ($round = 1; $round -le $FixRounds; $round++) {
    $roundId = "{0:D3}" -f $round
    $roundDir = Join-Path $OutputDir "round_$roundId"
    $reviewDir = Join-Path $roundDir "review"
    $fixDir = Join-Path $roundDir "fix"
    New-Item -ItemType Directory -Path $roundDir -Force | Out-Null

    Write-Host "[info] round ${roundId}: running review"
    Invoke-ReviewScript `
        -PowerShellExe $powerShellExe `
        -ScriptPath $reviewScriptPath `
        -OutputPath $reviewDir `
        -UseUncommittedMode:$Uncommitted `
        -DoDryRun:$DryRun `
        -BaseRef $Base `
        -ChunkSizeValue $ChunkSize `
        -SplitThresholdValue $SplitThreshold `
        -RetryCountValue $ReviewRetryCount `
        -CodexTimeoutSecondsValue $CodexTimeoutSeconds `
        -HealthCheckTimeoutSecondsValue $HealthCheckTimeoutSeconds `
        -FailFastOnCodexTimeoutValue $FailFastOnCodexTimeout `
        -SkipCodexHealthCheckValue $skipCodexHealthCheckBool `
        -ExtraInstructionsValue $ExtraReviewInstructions `
        -ExcludeGlobsValue $ExcludeGlobs `
        -ReviewModelValue $ReviewModel `
        -ReasoningEffortValue $ReasoningEffort

    $reviewManifestPath = Join-Path $reviewDir "review_manifest.json"
    $reviewManifest = Read-JsonFile -Path $reviewManifestPath
    $chunkStates = @(Get-ChunkFindings -Manifest $reviewManifest)

    $findingChunks = @($chunkStates | Where-Object { $_.has_findings -eq $true })
    $noFindingChunks = @($chunkStates | Where-Object { $_.has_findings -eq $false })
    $unknownChunks = @($chunkStates | Where-Object { $null -eq $_.has_findings })
    if ($unknownChunks.Count -gt 0) {
        $runManifest.unknown_results = $true
    }

    $roundInfo = [ordered]@{
        round              = $round
        review_dir         = $reviewDir
        total_chunks       = $chunkStates.Count
        finding_chunks     = $findingChunks.Count
        no_finding_chunks  = $noFindingChunks.Count
        unknown_chunks     = $unknownChunks.Count
        fix_attempted      = 0
        fix_succeeded      = 0
        fix_failed         = 0
        fix_skipped_reason = ""
        chunk_details      = @($chunkStates | ForEach-Object {
                [pscustomobject]@{
                    id               = $_.id
                    status           = $_.status
                    has_findings     = $_.has_findings
                    reason           = $_.reason
                    files_path       = $_.files_path
                    result_path      = $_.result_path
                    stderr_path      = $_.stderr_path
                    combined_path    = $_.combined_path
                    review_text_path = $_.review_text_path
                }
            })
    }

    if ($findingChunks.Count -eq 0 -and $unknownChunks.Count -eq 0) {
        $stoppedByNoFindings = $true
        $runManifest.no_findings_reached = $true
        $roundInfo.fix_skipped_reason = "no_findings"
        $runManifest.rounds += [pscustomobject]$roundInfo
        Write-Host "[ok] round ${roundId}: no findings"
        break
    }

    if ($DryRun) {
        $roundInfo.fix_skipped_reason = "dry_run"
        $runManifest.rounds += [pscustomobject]$roundInfo
        Write-Host "[info] round ${roundId}: dry-run, skipping fixes"
        break
    }

    if ($ReviewOnly) {
        $roundInfo.fix_skipped_reason = "review_only"
        $runManifest.rounds += [pscustomobject]$roundInfo
        Write-Host "[info] round ${roundId}: review-only, skipping fixes"
        break
    }

    if ($findingChunks.Count -eq 0) {
        $roundInfo.fix_skipped_reason = "unknown_review_results"
        $runManifest.rounds += [pscustomobject]$roundInfo
        Write-Host "[warn] round ${roundId}: findings unknown (review failures), skipping fixes"
        break
    }

    New-Item -ItemType Directory -Path $fixDir -Force | Out-Null
    $abortRoundFixByCircuit = $false
    foreach ($chunk in $findingChunks) {
        $chunkId = [string]$chunk.id
        $roundInfo.fix_attempted++

        $files = Read-FileLines -Path $chunk.files_path
        $reviewBundle = Get-ChunkReviewBundle -Chunk $chunk
        $reviewText = [string]$reviewBundle.effective_text

        $fixPrompt = Build-FixPrompt `
            -Files $files `
            -ReviewText $reviewText `
            -ScopeText $scopeText `
            -ChunkId $chunkId `
            -Round $round `
            -ExtraInstructions $ExtraFixInstructions `
            -MaxChars $MaxReviewCharsPerChunk

        $promptPath = Join-Path $fixDir "chunk_${chunkId}_fix_prompt.md"
        $resultPath = Join-Path $fixDir "chunk_${chunkId}_fix_result.txt"
        $stderrPath = Join-Path $fixDir "chunk_${chunkId}_fix_result.stderr.txt"
        $combinedPath = Join-Path $fixDir "chunk_${chunkId}_fix_result.combined.txt"
        $fixPrompt | Set-Content -Encoding utf8 $promptPath
        $scopeGuardDir = Join-Path $fixDir "chunk_${chunkId}_scope_guard"
        $scopeGuard = New-ScopeGuardSnapshot -RepoRoot $repoRoot -AllowedFiles $files -SnapshotRoot $scopeGuardDir

        Write-Host "[info] round ${roundId}: fixing chunk $chunkId"
        $fixResult = Invoke-CodexExecFix `
            -Prompt $fixPrompt `
            -Model $CodexModel `
            -EnableFullAuto:$UseFullAuto `
            -Retries $FixRetryCount `
            -TimeoutSeconds $CodexTimeoutSeconds `
            -CodexEntrypoint $codexEntrypoint `
            -WorkingDirectory $repoRoot `
            -StdoutPath $resultPath `
            -StderrPath $stderrPath `
            -CombinedPath $combinedPath

        if ($fixResult.ok) {
            $scopeCheck = Test-ScopeGuardViolations -RepoRoot $repoRoot -Snapshot $scopeGuard
            if ($scopeCheck.all_out_of_scope.Count -gt 0) {
                Write-Host "[warn] round ${roundId}: scope violation detected in chunk $chunkId"
                if ($scopeCheck.new_out_of_scope.Count -gt 0) {
                    Write-Host "[warn]   newly changed out-of-scope files: $($scopeCheck.new_out_of_scope -join ', ')"
                }
                if ($scopeCheck.mutated_existing_out_of_scope.Count -gt 0) {
                    Write-Host "[warn]   mutated existing out-of-scope files: $($scopeCheck.mutated_existing_out_of_scope -join ', ')"
                }
                Restore-ScopeGuardViolations -RepoRoot $repoRoot -Snapshot $scopeGuard -Paths $scopeCheck.all_out_of_scope
                $roundInfo.fix_failed++
                Write-Host "[warn] round ${roundId}: reverted out-of-scope changes and marked chunk $chunkId as failed"
                continue
            }
            $roundInfo.fix_succeeded++
            $appliedFixes = $true
            Write-Host "[ok] round ${roundId}: fixed chunk $chunkId (attempt $($fixResult.attempt))"
        }
        else {
            $roundInfo.fix_failed++
            Write-Host "[warn] round ${roundId}: fix failed for chunk $chunkId"
            $fatalFixTransport = $false
            if (Test-Path $combinedPath) {
                $fixCombinedText = Get-Content -Raw -Encoding utf8 $combinedPath
                if ($fixCombinedText -match '(?im)(not\s+logged\s+in|authentication|invalid\s+api\s+key|rate\s*limit|too\s+many\s+requests|network|timed?\s*out|econn|socket|tls|dns)') {
                    $fatalFixTransport = $true
                }
            }
            if ($FailFastOnCodexTimeout -and ([bool]$fixResult.timedOut -or $fatalFixTransport)) {
                Write-Host "[warn] round ${roundId}: stopping remaining fixes due to codex failure circuit"
                $abortRoundFixByCircuit = $true
                break
            }
        }
    }

    $runManifest.rounds += [pscustomobject]$roundInfo
    if ($abortRoundFixByCircuit) {
        break
    }
}

if (-not $DryRun -and -not $ReviewOnly -and -not $SkipFinalVerification -and $appliedFixes) {
    $finalDir = Join-Path $OutputDir "final_verification"
    Write-Host "[info] running final verification review"
    Invoke-ReviewScript `
        -PowerShellExe $powerShellExe `
        -ScriptPath $reviewScriptPath `
        -OutputPath $finalDir `
        -UseUncommittedMode:$Uncommitted `
        -DoDryRun:$false `
        -BaseRef $Base `
        -ChunkSizeValue $ChunkSize `
        -SplitThresholdValue $SplitThreshold `
        -RetryCountValue $ReviewRetryCount `
        -CodexTimeoutSecondsValue $CodexTimeoutSeconds `
        -HealthCheckTimeoutSecondsValue $HealthCheckTimeoutSeconds `
        -FailFastOnCodexTimeoutValue $FailFastOnCodexTimeout `
        -SkipCodexHealthCheckValue $skipCodexHealthCheckBool `
        -ExtraInstructionsValue $ExtraReviewInstructions `
        -ExcludeGlobsValue $ExcludeGlobs `
        -ReviewModelValue $ReviewModel `
        -ReasoningEffortValue $ReasoningEffort

    $finalManifest = Read-JsonFile -Path (Join-Path $finalDir "review_manifest.json")
    $finalStates = @(Get-ChunkFindings -Manifest $finalManifest)
    $finalFindings = @($finalStates | Where-Object { $_.has_findings -eq $true })
    $finalUnknown = @($finalStates | Where-Object { $null -eq $_.has_findings })
    if ($finalUnknown.Count -gt 0) {
        $runManifest.unknown_results = $true
    }
    if ($finalFindings.Count -eq 0 -and $finalUnknown.Count -eq 0) {
        $runManifest.no_findings_reached = $true
        $stoppedByNoFindings = $true
    }

    $runManifest.final_verification = [pscustomobject]@{
        review_dir        = $finalDir
        total_chunks      = $finalStates.Count
        finding_chunks    = $finalFindings.Count
        no_finding_chunks = (@($finalStates | Where-Object { $_.has_findings -eq $false })).Count
        unknown_chunks    = $finalUnknown.Count
    }
}

if ($stoppedByNoFindings) {
    Write-Host "[ok] no findings reached"
}

$runManifestPath = Join-Path $OutputDir "run_manifest.json"
$runManifest | ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8 $runManifestPath

$summary = @()
$summary += "# Codex Review and Fix Summary"
$summary += ""
$summary += "- generated_at: $((Get-Date).ToString('o'))"
$summary += "- branch: $currentBranch"
$summary += "- scope: $scopeText"
$summary += "- dry_run: $([bool]$DryRun)"
$summary += "- review_only: $([bool]$ReviewOnly)"
$summary += "- fix_rounds_requested: $FixRounds"
$summary += "- codex_timeout_seconds: $CodexTimeoutSeconds"
$summary += "- healthcheck_timeout_seconds: $HealthCheckTimeoutSeconds"
$summary += "- fail_fast_on_codex_timeout: $([bool]$FailFastOnCodexTimeout)"
$summary += "- no_findings_reached: $($runManifest.no_findings_reached)"
$summary += "- unknown_results: $($runManifest.unknown_results)"
$summary += ""
$summary += "## Rounds"

foreach ($round in @($runManifest.rounds)) {
    $summary += "- round_$('{0:D3}' -f [int]$round.round): findings=$($round.finding_chunks), no_findings=$($round.no_finding_chunks), unknown=$($round.unknown_chunks), fix_ok=$($round.fix_succeeded), fix_failed=$($round.fix_failed), review_dir=$($round.review_dir)"
}

if ($runManifest.final_verification) {
    $final = $runManifest.final_verification
    $summary += ""
    $summary += "## Final Verification"
    $summary += "- findings=$($final.finding_chunks), no_findings=$($final.no_finding_chunks), unknown=$($final.unknown_chunks), review_dir=$($final.review_dir)"
}

$summaryPath = Join-Path $OutputDir "summary.md"
$summary | Set-Content -Encoding utf8 $summaryPath

Write-Host "[ok] review-and-fix complete"
Write-Host "[ok] summary: $summaryPath"
Write-Host "[ok] manifest: $runManifestPath"
