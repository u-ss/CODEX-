# Antigravity Repository Architecture

## Zones

### 1) First-party source (review target)

- `.agent/workflows/ace-step/`
- `.agent/workflows/agi_kernel/`
- `.agent/workflows/blender/` — 子エージェント: `sub_agents/{character,house,image_world}/`
- `.agent/workflows/check/` — 子エージェント: `sub_agents/folder/`
- `.agent/workflows/code/`
- `.agent/workflows/codex/` — 子エージェント: `sub_agents/{app,cli_review,review}/`
- `.agent/workflows/desktop/`
- `.agent/workflows/desktop-chatgpt/`
- `.agent/workflows/diary/`
- `.agent/workflows/imagen/`
- `.agent/workflows/ki-learning/`
- `.agent/workflows/monitor/`
- `.agent/workflows/ops/`
- `.agent/workflows/orchestrator_pm/`
- `.agent/workflows/prompt/`
- `.agent/workflows/research/` — 子エージェント: `sub_agents/stealth_local/`
- `.agent/workflows/shared/`
- `.agent/workflows/video/` — 子エージェント: `sub_agents/{asset,audio,director,finalize,probe,remotion_props,renderer,shotlist,timing,voicevox}/`
- `.agent/workflows/voicebox/`
- `.agent/workflows/workflow_lint/`

These directories are part of the maintained product code and docs.

### 2) Vendor/archive area (non-review target by default)

- `.agent/workflows/claude-skills/`

This directory may include nested repositories and external assets.
Do not treat it as first-party implementation unless files are explicitly promoted.

### 3) Runtime artifact area (never commit)

- `_temp/`
- `_screenshots/`
- `_logs/`
- `_outputs/`
- `.agent/golden_profile/`
- `.agent/workflows/desktop/logs/`
- all `__pycache__/` and `*.pyc`

## Workflow policy

- Every first-party workflow directory must have both:
  - `SKILL.md`
  - `WORKFLOW.md`
- `SKILL.md` and `WORKFLOW.md` should carry matching semantic versions.
- Runtime outputs must not be tracked in Git.
- Legacy workflows that were consolidated are documented in `.agent/workflows/ops/WORKFLOW.md`.

## Validation

Run:

```bash
python tools/workflow_lint.py
```

## Promotion rule (vendor -> first-party)

When adopting external skills:

1. Copy only required files to a first-party workflow directory.
2. Remove external branding/assumptions not used in this repo.
3. Add/update `SKILL.md` and `WORKFLOW.md`.
4. Ensure `tools/workflow_lint.py` passes.
