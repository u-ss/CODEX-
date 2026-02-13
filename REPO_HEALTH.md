# Repository Health Report

Date: 2026-02-06

## Fixed in this pass

- Added root ignore rules for runtime artifacts, caches, logs, screenshots, and vendor dumps:
  - `.gitignore`
- Added baseline line-ending policy:
  - `.gitattributes`
- Removed tracked runtime artifacts from Git index (kept files on disk):
  - `_temp/*`, `_screenshots/*`, selected `__pycache__/*`, `.pyc` files, runtime logs
- Normalized workflow header consistency:
  - `.agent/workflows/agent-architect/WORKFLOW.md` now includes versioned title
- Added vendor policy note:
  - `.agent/workflows/claude-skills/README.antigravity.md`
- Removed BOM from key edited markdown/config files to avoid hidden diff noise.

## Current structural risks

- Large vendor dump under `.agent/workflows/claude-skills/` contains nested `.git` repositories and thousands of files.
- Runtime output directories are located inside repository root; this is now ignored, but historical tracked artifacts still need one cleanup commit.
- Workflow docs and implementation code evolve quickly; drift risk remains without automated consistency checks.

## Recommended next hardening steps

- Add CI check to fail on committed `__pycache__`, `.pyc`, `_temp`, `_screenshots`, `_logs`, `_outputs`.
- Add a workflow metadata linter:
  - enforce `SKILL.md` + `WORKFLOW.md` presence
  - enforce version header format
  - enforce command/tool naming policy
- Split vendor assets (`claude-skills`) into a separate repository or Git submodule outside `.agent/workflows/`.
- Add `docs/architecture.md` that defines:
  - first-party workflows
  - vendor/workbench areas
  - runtime artifact paths

## Local check command

```bash
python tools/workflow_lint.py
```

## Local cleanup command

```powershell
powershell -ExecutionPolicy Bypass -File tools/clean_runtime_artifacts.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File tools/clean_runtime_artifacts.ps1
```

> Safety default: `golden_profile` and `desktop/logs` are **not** deleted unless explicitly requested.

```powershell
# Optional aggressive cleanup
powershell -ExecutionPolicy Bypass -File tools/clean_runtime_artifacts.ps1 -IncludeDesktopWorkflowLogs
powershell -ExecutionPolicy Bypass -File tools/clean_runtime_artifacts.ps1 -IncludeGoldenProfile
```

## Local hygiene check

```bash
python tools/repo_hygiene_check.py
```
