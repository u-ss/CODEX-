param(
    [string]$Base = "main",
    [int]$ChunkSize = 8,
    [int]$SplitThreshold = 8,
    [int]$RetryCount = 2,
    [switch]$Uncommitted,
    [switch]$DryRun,
    [string]$OutputDir = "",
    [string]$ExtraInstructions = "",
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

# -File モードでカンマ区切り文字列として渡された場合のデシリアライズ
# 例: "a,b,c" → @("a","b","c")
if ($ExcludeGlobs.Count -eq 1 -and $ExcludeGlobs[0] -match ',') {
    $ExcludeGlobs = $ExcludeGlobs[0] -split ','
}

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

function Normalize-PathForGlob {
    param([Parameter(Mandatory = $true)][string]$Path)
    return ($Path -replace "\\", "/")
}

function Should-ExcludeFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Globs
    )
    $normalized = Normalize-PathForGlob -Path $Path
    foreach ($glob in $Globs) {
        if ($normalized -like $glob) {
            return $true
        }
    }
    return $false
}

function Get-ChangedFilesFromBase {
    param([Parameter(Mandatory = $true)][string]$BaseRef)
    $files = & git -c core.quotepath=false diff --name-only "$BaseRef...HEAD"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to run: git diff --name-only $BaseRef...HEAD"
    }
    return @($files | Where-Object { $_ -and $_.Trim().Length -gt 0 })
}

# サブモジュールのパスを取得（mode 160000 = gitlink）
function Get-SubmodulePaths {
    $prevEap = $ErrorActionPreference
    $entries = @()
    try {
        $ErrorActionPreference = "Continue"
        $raw = @(& git ls-files --stage)
        $entries = $raw
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    $set = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::Ordinal)
    foreach ($line in $entries) {
        $text = [string]$line
        # フォーマット: "160000 <hash> <stage>\t<path>"
        if ($text -match '^160000\s') {
            $path = ($text -split '\t', 2)[1]
            if ($path) { [void]$set.Add($path.Trim()) }
        }
    }
    return , $set
}

function Get-UncommittedFiles {
    $submodules = Get-SubmodulePaths
    $unstaged = @(& git -c core.quotepath=false diff --name-only)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to run: git diff --name-only"
    }
    $staged = @(& git -c core.quotepath=false diff --name-only --cached)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to run: git diff --name-only --cached"
    }
    $untracked = @(& git -c core.quotepath=false ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to run: git ls-files --others --exclude-standard"
    }
    return @($unstaged + $staged + $untracked | Where-Object {
            $_ -and $_.Trim().Length -gt 0 -and -not $submodules.Contains($_.Trim())
        } | Where-Object {
            # ディレクトリ（末尾が/）を除外し、実在するファイルのみ対象
            $trimmed = $_.Trim()
            -not $trimmed.EndsWith('/') -and -not (Test-Path $trimmed -PathType Container)
        } | Sort-Object -Unique)
}

function Split-IntoChunks {
    param(
        [Parameter(Mandatory = $true)][string[]]$Items,
        [Parameter(Mandatory = $true)][int]$Size
    )
    if ($Items.Count -eq 0) {
        return @()
    }
    if ($Size -lt 1) {
        throw "Chunk size must be >= 1"
    }
    $chunks = @()
    for ($index = 0; $index -lt $Items.Count; $index += $Size) {
        $count = [Math]::Min($Size, $Items.Count - $index)
        $chunks += , @($Items[$index..($index + $count - 1)])
    }
    return $chunks
}

function Build-ReviewPrompt {
    param(
        [Parameter(Mandatory = $true)][string[]]$Files,
        [Parameter(Mandatory = $true)][bool]$UseUncommitted,
        [Parameter(Mandatory = $true)][string]$BaseRef,
        [AllowEmptyString()][string]$Extra = ""
    )
    $scope = if ($UseUncommitted) { "uncommitted changes" } else { "diff against base '$BaseRef'" }
    $filesBlock = ($Files | ForEach-Object { '- `{0}`' -f $_ }) -join "`n"
    $extraBlock = if ([string]::IsNullOrWhiteSpace($Extra)) { "" } else { "`nAdditional instructions:`n$Extra`n" }
    $prompt = @"
Review only the files listed below. Ignore all other files even if other diffs exist.
Scope: $scope

Files in scope:
$filesBlock

Output requirements:
1. List findings ordered by severity: ERROR, CAUTION, ADVISORY.
2. For each finding, include file path and line number.
3. Include a concrete fix recommendation.
4. If no findings exist in scoped files, output exactly: No findings in scoped files.
5. Wrap the final answer with the exact markers below:
<<REVIEW_RESULT>>
...
<</REVIEW_RESULT>>
$extraBlock
"@
    return $prompt
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

function Test-CodexApiHealth {
    param(
        [Parameter(Mandatory = $true)]$CodexEntrypoint,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$OutputDir,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    $stdoutPath = Join-Path $OutputDir "codex_healthcheck.stdout.txt"
    $stderrPath = Join-Path $OutputDir "codex_healthcheck.stderr.txt"
    $combinedPath = Join-Path $OutputDir "codex_healthcheck.combined.txt"
    $healthToken = "__CODEX_HEALTH_OK__"
    $prompt = "Return exactly: $healthToken"

    $nodePath = [string]$CodexEntrypoint.nodePath
    $codexJs = [string]$CodexEntrypoint.codexJs
    $argsList = @($codexJs, "exec", "-")

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $nodePath
    $psi.Arguments = ($argsList | ForEach-Object {
            $a = [string]$_
            if ($a -match '[\s"]') {
                $escaped = $a -replace '"', '\"'
                '"{0}"' -f $escaped
            }
            else { $a }
        }) -join ' '
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $timedOut = $false
    $stdoutText = ""
    $stderrText = ""
    $exitCode = 1
    try {
        $null = $proc.Start()
        $proc.StandardInput.Write($prompt)
        $proc.StandardInput.Close()

        $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
        $stderrTask = $proc.StandardError.ReadToEndAsync()
        $timeoutMs = $TimeoutSeconds * 1000
        $exited = $proc.WaitForExit($timeoutMs)
        if (-not $exited) {
            $timedOut = $true
            Stop-ProcessTree -Process $proc
            try { $proc.WaitForExit(5000) } catch {}
        }
        $stdoutText = Read-TaskResultText -Task $stdoutTask -TimeoutMs 10000
        $stderrText = Read-TaskResultText -Task $stderrTask -TimeoutMs 10000
        if ($timedOut) {
            if ([string]::IsNullOrWhiteSpace($stdoutText)) {
                $stdoutText = "TIMEOUT: healthcheck killed after ${TimeoutSeconds}s"
            }
            if ([string]::IsNullOrWhiteSpace($stderrText)) {
                $stderrText = "TIMEOUT: healthcheck killed after ${TimeoutSeconds}s"
            }
            $exitCode = 124
        }
        else {
            $exitCode = $proc.ExitCode
        }
    }
    catch {
        $stdoutText = ""
        $stderrText = "EXCEPTION: $($_.Exception.Message)"
        $exitCode = 1
    }
    finally {
        $proc.Dispose()
    }

    Write-Utf8NoBom -Path $stdoutPath -Text $stdoutText
    Write-Utf8NoBom -Path $stderrPath -Text $stderrText
    Write-CombinedOutput `
        -Path $combinedPath `
        -Mode "healthcheck" `
        -Attempt 1 `
        -ExitCode $exitCode `
        -TimedOut:$timedOut `
        -StdoutText $stdoutText `
        -StderrText $stderrText

    $allText = ($stdoutText + "`n" + $stderrText)
    $healthy = ($exitCode -eq 0 -and -not $timedOut -and $allText -match [regex]::Escape($healthToken))
    return @{
        healthy       = $healthy
        exitCode      = $exitCode
        timedOut      = $timedOut
        stdout_path   = $stdoutPath
        stderr_path   = $stderrPath
        combined_path = $combinedPath
    }
}

function Test-CodexLoggedIn {
    param(
        [Parameter(Mandatory = $true)]$CodexEntrypoint,
        [Parameter(Mandatory = $true)][string]$TempDir
    )

    $nodePath = [string]$CodexEntrypoint.nodePath
    $codexJs = [string]$CodexEntrypoint.codexJs

    $stdoutPath = Join-Path $TempDir "codex_login_status.stdout.txt"
    $stderrPath = Join-Path $TempDir "codex_login_status.stderr.txt"
    if (Test-Path $stdoutPath) { Remove-Item -Force $stdoutPath }
    if (Test-Path $stderrPath) { Remove-Item -Force $stderrPath }

    $argString = ('"{0}" login status' -f $codexJs)
    $proc = Start-Process -FilePath $nodePath `
        -ArgumentList $argString `
        -WorkingDirectory $TempDir `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    $stdout = if (Test-Path $stdoutPath) { Get-Content -Raw -Encoding utf8 $stdoutPath } else { "" }
    $stderr = if (Test-Path $stderrPath) { Get-Content -Raw -Encoding utf8 $stderrPath } else { "" }
    $text = ($stdout + "`n" + $stderr).Trim()

    if ($proc.ExitCode -ne 0 -or $text -match "(?im)not\s+logged\s+in") {
        throw @"
Codex CLI is not logged in.

Run one of the following and retry:
  - codex login --device-auth   # recommended (ChatGPT login)
  - codex login --with-api-key  # API key via stdin
"@
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

function Get-GitDiffText {
    param(
        [Parameter(Mandatory = $true)][string[]]$GitArgs,
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )
    # PowerShellパイプラインやStart-Processではシステムエンコーディング(Shift_JIS)に変換されるため
    # System.Diagnostics.Processで直接UTF-8出力を取得する
    $allArgs = @("-C", $RepoRoot, "-c", "core.quotepath=false") + $GitArgs
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "git"
    # 引数のエスケープ: 空白・引用符・特殊文字を含む場合に適切にクォート
    $psi.Arguments = ($allArgs | ForEach-Object {
            $a = [string]$_
            if ($a -match '[\s"]') {
                $escaped = $a -replace '"', '\"'
                '"{0}"' -f $escaped
            }
            else { $a }
        }) -join " "
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = $RepoRoot

    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    if ($proc.ExitCode -gt 1) {
        throw "Failed to run: git $($GitArgs -join ' ')`n$stderr"
    }
    return $stdout
}

function Get-UntrackedFilesSet {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $output = @(& git -C $RepoRoot -c core.quotepath=false ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to run: git ls-files --others --exclude-standard"
    }
    $set = New-Object "System.Collections.Generic.HashSet[string]" ([System.StringComparer]::Ordinal)
    foreach ($line in $output) {
        $value = [string]$line
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            [void]$set.Add($value.Trim())
        }
    }
    # PowerShellはreturnで空コレクションを列挙→$nullに変換するため、カンマ演算子で抑制
    return , $set
}

function Build-ChunkPatch {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][bool]$UseUncommitted,
        [Parameter(Mandatory = $true)][string]$BaseRef,
        [Parameter(Mandatory = $true)][string]$WorktreeBaseCommit,
        [Parameter(Mandatory = $true)][string[]]$Files
    )

    if ($UseUncommitted) {
        $untracked = Get-UntrackedFilesSet -RepoRoot $RepoRoot
        $parts = New-Object "System.Collections.Generic.List[string]"

        $submodules = Get-SubmodulePaths
        foreach ($file in $Files) {
            # サブモジュールはgit applyで適用できないためスキップ
            if ($submodules.Contains($file)) { continue }
            # ディレクトリはスキップ（git diff対象外）
            $fullPath = Join-Path $RepoRoot $file
            if (Test-Path $fullPath -PathType Container) { continue }
            if ($untracked.Contains($file)) {
                # Windowsには /dev/null が無いため、空ファイルを使用
                $emptyFile = Join-Path $env:TEMP "codex_review_empty_file"
                if (-not (Test-Path $emptyFile)) { New-Item -ItemType File -Path $emptyFile -Force | Out-Null }
                $part = Get-GitDiffText -RepoRoot $RepoRoot -GitArgs @("diff", "--no-index", "--", $emptyFile, $file)
                if (-not [string]::IsNullOrWhiteSpace($part)) { [void]$parts.Add($part) }
                continue
            }

            $staged = Get-GitDiffText -RepoRoot $RepoRoot -GitArgs @("diff", "--cached", "--", $file)
            if (-not [string]::IsNullOrWhiteSpace($staged)) { [void]$parts.Add($staged) }

            $unstaged = Get-GitDiffText -RepoRoot $RepoRoot -GitArgs @("diff", "--", $file)
            if (-not [string]::IsNullOrWhiteSpace($unstaged)) { [void]$parts.Add($unstaged) }
        }

        return ($parts -join "`n")
    }

    # Base compare: build patch from the merge-base commit to HEAD so it can be applied cleanly.
    $head = @(& git -C $RepoRoot rev-parse HEAD)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to run: git rev-parse HEAD"
    }
    $headCommit = ($head | Select-Object -First 1).Trim()

    # Base側でもサブモジュール（mode 160000）を除外
    $submodules = Get-SubmodulePaths
    $filteredFiles = @($Files | Where-Object { -not $submodules.Contains($_) })
    if ($filteredFiles.Count -eq 0) {
        return ""
    }
    $gitDiffArgs = @("diff", $WorktreeBaseCommit, $headCommit, "--") + $filteredFiles
    return (Get-GitDiffText -RepoRoot $RepoRoot -GitArgs $gitDiffArgs)
}

function Reset-WorktreeState {
    param([Parameter(Mandatory = $true)][string]$WorktreeRoot)
    $prevEap = $ErrorActionPreference
    $resetCode = 0
    $cleanCode = 0
    try {
        $ErrorActionPreference = "Continue"
        & git -C $WorktreeRoot reset --hard *> $null
        $resetCode = $LASTEXITCODE
        & git -C $WorktreeRoot clean -fdx *> $null
        $cleanCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    if ($resetCode -ne 0) {
        throw "Failed to reset worktree: $WorktreeRoot"
    }
    if ($cleanCode -ne 0) {
        throw "Failed to clean worktree: $WorktreeRoot"
    }
}

function Apply-PatchToWorktree {
    param(
        [Parameter(Mandatory = $true)][string]$WorktreeRoot,
        [Parameter(Mandatory = $true)][string]$PatchPath
    )
    $prevEap = $ErrorActionPreference
    $code = 0
    $output = @()
    try {
        $ErrorActionPreference = "Continue"
        # まず通常applyを試行
        $output = @(& git -C $WorktreeRoot apply --whitespace=nowarn $PatchPath 2>&1)
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            # 失敗時は --3way フォールバック
            Write-Host "[warn] normal apply failed, trying --3way fallback"
            $output = @(& git -C $WorktreeRoot apply --whitespace=nowarn --3way $PatchPath 2>&1)
            $code = $LASTEXITCODE
        }
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    if ($code -ne 0) {
        $details = ($output | ForEach-Object { $_.ToString() }) -join "`n"
        throw "Failed to apply patch in worktree: $PatchPath`n$details"
    }
}

function Invoke-CodexReviewChunk {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath,
        [Parameter(Mandatory = $true)][string]$CombinedPath,
        [Parameter(Mandatory = $true)][int]$Retries,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)]$CodexEntrypoint,
        [AllowEmptyString()][string]$Model = "",
        [AllowEmptyString()][string]$Effort = ""
    )
    $attempt = 0
    $lastMode = "unknown"
    $lastTimedOut = $false
    while ($attempt -le $Retries) {
        $attempt++

        $stdinPath = Join-Path (Split-Path -Parent $StdoutPath) ("codex_stdin_attempt_{0:D3}.txt" -f $attempt)
        Write-Utf8NoBom -Path $stdinPath -Text ($Prompt + "`n")

        $nodePath = [string]$CodexEntrypoint.nodePath
        $codexJs = [string]$CodexEntrypoint.codexJs

        # 1回目は review プリセット、2回目以降は通常 exec でフォールバック
        $useReviewPreset = ($attempt -eq 1)
        $argsList = @($codexJs, 'exec')
        if ($useReviewPreset) {
            $argsList += 'review'
        }
        $mode = if ($useReviewPreset) { "exec_review" } else { "exec" }
        $lastMode = $mode
        if (-not [string]::IsNullOrWhiteSpace($Model)) {
            $argsList += @('-c', ('model="{0}"' -f $Model))
        }
        if (-not [string]::IsNullOrWhiteSpace($Effort)) {
            $argsList += @('-c', ('reasoning_effort="{0}"' -f $Effort))
        }
        $argsList += '-'

        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $nodePath
        # 引数のエスケープ: 空白・引用符・特殊文字を含む場合に適切にクォート
        $psi.Arguments = ($argsList | ForEach-Object {
                $a = [string]$_
                if ($a -match '[\s"]') {
                    $escaped = $a -replace '"', '\"'
                    '"{0}"' -f $escaped
                }
                else { $a }
            }) -join ' '
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
            # stdin にプロンプトを書き込み → 閉じる
            $proc.StandardInput.Write($Prompt)
            $proc.StandardInput.Close()

            # stdout/stderr を非同期で読み取り（デッドロック防止）
            $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
            $stderrTask = $proc.StandardError.ReadToEndAsync()
            $timeoutMs = $TimeoutSeconds * 1000
            $exited = $proc.WaitForExit($timeoutMs)
            $timedOut = $false

            if (-not $exited) {
                # タイムアウト: プロセスを強制終了
                Write-Host "[warn] codex review process timed out after ${TimeoutSeconds}s, killing..."
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
                -Mode $mode `
                -Attempt $attempt `
                -ExitCode $exitCode `
                -TimedOut:$timedOut `
                -StdoutText $stdoutText `
                -StderrText $stderrText

            if ($exitCode -eq 0 -and [string]::IsNullOrWhiteSpace($stdoutText) -and [string]::IsNullOrWhiteSpace($stderrText)) {
                $exitCode = 125
                Write-Utf8NoBom -Path $StderrPath -Text "EMPTY_OUTPUT: codex returned success but both stdout/stderr were empty."
                Write-CombinedOutput `
                    -Path $CombinedPath `
                    -Mode $mode `
                    -Attempt $attempt `
                    -ExitCode $exitCode `
                    -TimedOut:$false `
                    -StdoutText "" `
                    -StderrText "EMPTY_OUTPUT: codex returned success but both stdout/stderr were empty."
            }
            $lastTimedOut = $timedOut
        }
        catch {
            Write-Host "[warn] codex exec review attempt $attempt exception: $($_.Exception.Message)"
            $exitCode = 1
            Write-Utf8NoBom -Path $StdoutPath -Text ""
            Write-Utf8NoBom -Path $StderrPath -Text ("EXCEPTION: " + $_.Exception.Message)
            Write-CombinedOutput `
                -Path $CombinedPath `
                -Mode $mode `
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
                mode     = $mode
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
        mode     = $lastMode
        timedOut = $lastTimedOut
    }
}

$repoRoot = Get-RepoRoot
Set-Location $repoRoot

if (-not (Test-CommandExists -Name "git")) {
    throw "git command not found"
}
if (-not $DryRun -and -not (Test-CommandExists -Name "codex")) {
    throw "codex command not found"
}
if ($CodexTimeoutSeconds -lt 30) {
    throw "CodexTimeoutSeconds must be >= 30"
}
if ($HealthCheckTimeoutSeconds -lt 10) {
    throw "HealthCheckTimeoutSeconds must be >= 10"
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
    $OutputDir = Join-Path $repoRoot "_outputs/review/codex_cli/$timestamp"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

Write-Host "[info] repo root: $repoRoot"
Write-Host "[info] output dir: $OutputDir"
Write-Host "[info] current branch: $currentBranch"
Write-Host "[info] mode: $([string]::Join('', @(if ($Uncommitted) { 'uncommitted' } else { "base=$Base" })))"

$allChangedFiles = @(if ($Uncommitted) { Get-UncommittedFiles } else { Get-ChangedFilesFromBase -BaseRef $Base })
$filteredFiles = @(
    $allChangedFiles |
    Where-Object { -not (Should-ExcludeFile -Path $_ -Globs $ExcludeGlobs) } |
    Sort-Object -Unique
)

$manifest = @{
    generated_at                = (Get-Date).ToString("o")
    repo_root                   = $repoRoot
    branch                      = $currentBranch
    mode                        = if ($Uncommitted) { "uncommitted" } else { "base_compare" }
    base                        = if ($Uncommitted) { $null } else { $Base }
    dry_run                     = [bool]$DryRun
    chunk_size                  = $ChunkSize
    split_threshold             = $SplitThreshold
    retry_count                 = $RetryCount
    codex_timeout_seconds       = $CodexTimeoutSeconds
    healthcheck_timeout_seconds = $HealthCheckTimeoutSeconds
    fail_fast_on_codex_timeout  = [bool]$FailFastOnCodexTimeout
    skip_codex_healthcheck      = [bool]$SkipCodexHealthCheck
    codex_healthcheck           = $null
    circuit_opened              = $false
    circuit_reason              = ""
    total_changed               = $allChangedFiles.Count
    total_filtered              = $filteredFiles.Count
    excluded_globs              = $ExcludeGlobs
    chunks                      = @()
}

if ($filteredFiles.Count -eq 0) {
    $manifestPath = Join-Path $OutputDir "review_manifest.json"
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $manifestPath
    "No review target files after filtering." | Set-Content -Encoding utf8 (Join-Path $OutputDir "summary.md")
    Write-Host "[ok] no review targets after filtering."
    return
}

$effectiveChunks = @()
if ($filteredFiles.Count -le $SplitThreshold) {
    $effectiveChunks = , $filteredFiles
}
else {
    $effectiveChunks = Split-IntoChunks -Items $filteredFiles -Size $ChunkSize
}

Write-Host "[info] changed files: $($filteredFiles.Count), chunks: $($effectiveChunks.Count)"

$codexEntrypoint = $null
$worktreeRoot = ""
$worktreeBaseCommit = ""
$codexCircuitOpen = $false
$codexCircuitReason = ""

try {
    if (-not $DryRun) {
        $codexEntrypoint = Get-CodexCliEntrypoint
        Test-CodexLoggedIn -CodexEntrypoint $codexEntrypoint -TempDir $OutputDir

        if ($Uncommitted) {
            $worktreeBaseCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to resolve HEAD commit"
            }
        }
        else {
            $worktreeBaseCommit = (& git -C $repoRoot merge-base $Base HEAD).Trim()
            if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($worktreeBaseCommit)) {
                throw "Failed to compute merge-base for '$Base' and HEAD"
            }
        }

        $worktreeRoot = Join-Path $repoRoot "_temp/codex_cli_review_worktree/$timestamp"
        New-Item -ItemType Directory -Path (Split-Path -Parent $worktreeRoot) -Force | Out-Null

        $prevEap = $ErrorActionPreference
        $wtCode = 0
        try {
            $ErrorActionPreference = "Continue"
            & git -C $repoRoot worktree add --detach $worktreeRoot $worktreeBaseCommit *> $null
            $wtCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $prevEap
        }

        if ($wtCode -ne 0) {
            throw "Failed to create git worktree: $worktreeRoot"
        }

        if (-not $SkipCodexHealthCheck) {
            Write-Host "[info] running codex API healthcheck (${HealthCheckTimeoutSeconds}s timeout)"
            $health = Test-CodexApiHealth `
                -CodexEntrypoint $codexEntrypoint `
                -WorkingDirectory $worktreeRoot `
                -OutputDir $OutputDir `
                -TimeoutSeconds $HealthCheckTimeoutSeconds
            $manifest.codex_healthcheck = [pscustomobject]$health
            if (-not $health.healthy) {
                throw "Codex healthcheck failed (exit=$($health.exitCode), timeout=$($health.timedOut)). See: $($health.combined_path)"
            }
        }
    }

    for ($i = 0; $i -lt $effectiveChunks.Count; $i++) {
        $chunkNumber = $i + 1
        $chunkId = "{0:D3}" -f $chunkNumber
        $files = @($effectiveChunks[$i])

        $chunkFilesPath = Join-Path $OutputDir "chunk_${chunkId}_files.txt"
        $files | Set-Content -Encoding utf8 $chunkFilesPath

        $prompt = Build-ReviewPrompt -Files $files -UseUncommitted:$Uncommitted -BaseRef $Base -Extra $ExtraInstructions
        $promptPath = Join-Path $OutputDir "chunk_${chunkId}_prompt.md"
        Write-Utf8NoBom -Path $promptPath -Text $prompt
        $resultPath = Join-Path $OutputDir "chunk_${chunkId}_review.txt"
        $stderrPath = Join-Path $OutputDir "chunk_${chunkId}_review.stderr.txt"
        $combinedPath = Join-Path $OutputDir "chunk_${chunkId}_review.combined.txt"
        $patchPath = Join-Path $OutputDir "chunk_${chunkId}_patch.diff"

        if ($codexCircuitOpen -and -not $DryRun) {
            Write-Host "[warn] skipping chunk $chunkId due to open codex circuit: $codexCircuitReason"
            Write-Utf8NoBom -Path $resultPath -Text ""
            Write-Utf8NoBom -Path $stderrPath -Text ("CIRCUIT_OPEN: " + $codexCircuitReason)
            Write-CombinedOutput `
                -Path $combinedPath `
                -Mode "circuit_open" `
                -Attempt 0 `
                -ExitCode 1 `
                -TimedOut:$false `
                -StdoutText "" `
                -StderrText ("CIRCUIT_OPEN: " + $codexCircuitReason)
            $manifest.chunks += @{
                id             = $chunkId
                file_count     = $files.Count
                files_path     = $chunkFilesPath
                prompt_path    = $promptPath
                result_path    = $resultPath
                status         = "SKIPPED_CIRCUIT_OPEN"
                codex_exitcode = $null
                attempts       = 0
                patch_path     = $patchPath
                stderr_path    = $stderrPath
                combined_path  = $combinedPath
                command_mode   = "circuit_open"
                timed_out      = $false
                error          = $codexCircuitReason
            }
            continue
        }

        if ($DryRun) {
            Write-Host "[dry-run] chunk $chunkId prepared ($($files.Count) files)"
            $manifest.chunks += @{
                id             = $chunkId
                file_count     = $files.Count
                files_path     = $chunkFilesPath
                prompt_path    = $promptPath
                result_path    = $null
                status         = "DRY_RUN"
                codex_exitcode = $null
                attempts       = 0
                stderr_path    = $null
                combined_path  = $null
                command_mode   = $null
                timed_out      = $false
            }
            continue
        }

        if (-not $worktreeRoot) {
            throw "Internal error: worktree is not initialized"
        }

        Write-Host "[info] reviewing chunk $chunkId ($($files.Count) files)"
        $result = $null
        $chunkStatus = "FAILED"
        $chunkError = ""
        $patchText = ""

        try {
            Reset-WorktreeState -WorktreeRoot $worktreeRoot
            $patchText = Build-ChunkPatch -RepoRoot $repoRoot -UseUncommitted:$Uncommitted -BaseRef $Base -WorktreeBaseCommit $worktreeBaseCommit -Files $files
            Write-Utf8NoBom -Path $patchPath -Text ($patchText + "`n")

            if ([string]::IsNullOrWhiteSpace($patchText)) {
                Write-Host "[warn] chunk $chunkId has empty patch after filtering; treating as no-findings"
                Write-Utf8NoBom -Path $resultPath -Text "No findings in scoped files."
                Write-Utf8NoBom -Path $stderrPath -Text ""
                Write-CombinedOutput `
                    -Path $combinedPath `
                    -Mode "skipped_empty_patch" `
                    -Attempt 0 `
                    -ExitCode 0 `
                    -TimedOut:$false `
                    -StdoutText "No findings in scoped files." `
                    -StderrText ""
                $result = @{
                    ok       = $true
                    attempt  = 0
                    exitCode = 0
                    mode     = "skipped_empty_patch"
                    timedOut = $false
                }
                $chunkStatus = "SKIPPED_EMPTY_PATCH"
            }
            else {
                Apply-PatchToWorktree -WorktreeRoot $worktreeRoot -PatchPath $patchPath
                $result = Invoke-CodexReviewChunk `
                    -Prompt $prompt `
                    -WorkingDirectory $worktreeRoot `
                    -StdoutPath $resultPath `
                    -StderrPath $stderrPath `
                    -CombinedPath $combinedPath `
                    -Retries $RetryCount `
                    -TimeoutSeconds $CodexTimeoutSeconds `
                    -CodexEntrypoint $codexEntrypoint `
                    -Model $ReviewModel `
                    -Effort $ReasoningEffort
                # パイプライン出力漏れで$resultが配列になる場合のフィルタ
                if ($result -is [array]) {
                    $result = $result | Where-Object { $_ -is [hashtable] } | Select-Object -Last 1
                }
                if ($null -eq $result -or $result -isnot [hashtable]) {
                    $result = @{
                        ok       = $false
                        attempt  = 0
                        exitCode = 1
                        mode     = "unknown_return"
                        timedOut = $false
                    }
                }
                $chunkStatus = if ($result.ok) { "OK" } else { "FAILED" }
            }
        }
        catch {
            $chunkError = $_.Exception.Message
            Write-Host "[warn] chunk $chunkId runtime error: $chunkError"
            if (-not (Test-Path $resultPath)) {
                Write-Utf8NoBom -Path $resultPath -Text ""
            }
            Write-Utf8NoBom -Path $stderrPath -Text ("CHUNK_RUNTIME_ERROR: " + $chunkError)
            Write-CombinedOutput `
                -Path $combinedPath `
                -Mode "runtime_error" `
                -Attempt 0 `
                -ExitCode 1 `
                -TimedOut:$false `
                -StdoutText "" `
                -StderrText ("CHUNK_RUNTIME_ERROR: " + $chunkError)
            $result = @{
                ok       = $false
                attempt  = 0
                exitCode = 1
                mode     = "runtime_error"
                timedOut = $false
            }
            $chunkStatus = "FAILED"
        }

        $manifest.chunks += @{
            id             = $chunkId
            file_count     = $files.Count
            files_path     = $chunkFilesPath
            prompt_path    = $promptPath
            result_path    = $resultPath
            status         = $chunkStatus
            codex_exitcode = if ($chunkStatus -eq "SKIPPED_EMPTY_PATCH") { $null } elseif ($null -ne $result) { $result.exitCode } else { $null }
            attempts       = if ($null -ne $result) { $result.attempt } else { 0 }
            patch_path     = $patchPath
            stderr_path    = $stderrPath
            combined_path  = $combinedPath
            command_mode   = if ($null -ne $result) { $result.mode } else { "unknown" }
            timed_out      = if ($null -ne $result) { [bool]$result.timedOut } else { $false }
            error          = if ([string]::IsNullOrWhiteSpace($chunkError)) { $null } else { $chunkError }
        }

        if ((-not ($null -ne $result -and $result.ok)) -and $chunkStatus -ne "SKIPPED_EMPTY_PATCH") {
            Write-Host "[warn] chunk $chunkId failed after retries"
            $fatalTransport = $false
            if (Test-Path $combinedPath) {
                $combinedText = Get-Content -Raw -Encoding utf8 $combinedPath
                if ($combinedText -match '(?im)(not\s+logged\s+in|authentication|auth|rate\s*limit|too\s+many\s+requests|network|timed?\s*out|econn|socket|tls|dns)') {
                    $fatalTransport = $true
                }
            }
            $resultTimedOut = if ($null -ne $result) { [bool]$result.timedOut } else { $false }
            if ($FailFastOnCodexTimeout -and ($resultTimedOut -or $fatalTransport)) {
                $codexCircuitOpen = $true
                if ($resultTimedOut) {
                    $codexCircuitReason = "timeout_on_chunk_$chunkId"
                }
                else {
                    $codexCircuitReason = "transport_or_auth_failure_on_chunk_$chunkId"
                }
                $manifest.circuit_opened = $true
                $manifest.circuit_reason = $codexCircuitReason
                Write-Host "[warn] opening codex circuit breaker: $codexCircuitReason"
            }
        }
    }

    $summaryLines = @()
    $summaryLines += "# Codex CLI Diff Review Summary"
    $summaryLines += ""
    $summaryLines += "- generated_at: $((Get-Date).ToString('o'))"
    $summaryLines += "- branch: $currentBranch"
    if ($Uncommitted) {
        $summaryLines += "- mode: uncommitted"
    }
    else {
        $summaryLines += "- mode: base compare ($Base...HEAD)"
    }
    $summaryLines += "- total_changed_files: $($allChangedFiles.Count)"
    $summaryLines += "- total_review_files: $($filteredFiles.Count)"
    $summaryLines += "- chunk_count: $($effectiveChunks.Count)"
    $summaryLines += "- dry_run: $([bool]$DryRun)"
    $summaryLines += "- codex_timeout_seconds: $CodexTimeoutSeconds"
    $summaryLines += "- healthcheck_timeout_seconds: $HealthCheckTimeoutSeconds"
    $summaryLines += "- circuit_opened: $($manifest.circuit_opened)"
    if ($manifest.circuit_opened) {
        $summaryLines += "- circuit_reason: $($manifest.circuit_reason)"
    }
    $summaryLines += ""
    $summaryLines += "## Chunks"

    foreach ($chunk in $manifest.chunks) {
        $summaryLines += "- chunk_$($chunk.id): status=$($chunk.status), mode=$($chunk.command_mode), timed_out=$($chunk.timed_out), files=$($chunk.file_count), attempts=$($chunk.attempts), result=$($chunk.result_path)"
    }

    $summaryPath = Join-Path $OutputDir "summary.md"
    $summaryLines | Set-Content -Encoding utf8 $summaryPath

    $manifestPath = Join-Path $OutputDir "review_manifest.json"
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $manifestPath

    Write-Host "[ok] review run complete"
    Write-Host "[ok] summary: $summaryPath"
    Write-Host "[ok] manifest: $manifestPath"

}
catch {
    $fatalError = $_.Exception.Message
    $manifest.fatal_error = $fatalError

    $manifestPath = Join-Path $OutputDir "review_manifest.json"
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $manifestPath

    $summaryPath = Join-Path $OutputDir "summary.md"
    $failedSummary = @()
    $failedSummary += "# Codex CLI Diff Review Summary"
    $failedSummary += ""
    $failedSummary += "- generated_at: $((Get-Date).ToString('o'))"
    $failedSummary += "- status: FAILED"
    $failedSummary += "- fatal_error: $fatalError"
    $failedSummary += "- chunk_count: $($effectiveChunks.Count)"
    $failedSummary += "- output_dir: $OutputDir"
    $failedSummary | Set-Content -Encoding utf8 $summaryPath

    Write-Host "[error] review run failed: $fatalError"
    Write-Host "[error] summary: $summaryPath"
    Write-Host "[error] manifest: $manifestPath"
    throw
}
finally {
    if ($worktreeRoot -and (Test-Path $worktreeRoot)) {
        $prevEap = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & git -C $repoRoot worktree remove --force $worktreeRoot *> $null
            $removeCode = $LASTEXITCODE
            & git -C $repoRoot worktree prune *> $null
            $pruneCode = $LASTEXITCODE
            if ($removeCode -ne 0) {
                Write-Host "[warn] worktree remove failed (exit=$removeCode): $worktreeRoot"
            }
            if ($pruneCode -ne 0) {
                Write-Host "[warn] worktree prune failed (exit=$pruneCode)"
            }
        }
        catch {
            Write-Host "[warn] worktree cleanup exception: $($_.Exception.Message)"
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
    }
}
