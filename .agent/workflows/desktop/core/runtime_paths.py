# -*- coding: utf-8 -*-
"""
Desktop workflow runtime path resolver.

優先順位:
1. 環境変数
2. リポジトリ既定値 (_outputs/desktop)
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_BASE_DIR = "AG_DESKTOP_BASE_DIR"
ENV_SCREENSHOT_DIR = "AG_DESKTOP_SCREENSHOT_DIR"
ENV_TEMPLATE_STORE_PATH = "AG_DESKTOP_TEMPLATE_STORE_PATH"


def _repo_root() -> Path:
    # .agent/workflows/desktop/core/runtime_paths.py -> repo root
    return Path(__file__).resolve().parents[4]


def get_desktop_base_dir() -> Path:
    raw = os.getenv(ENV_BASE_DIR, "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo_root() / "_outputs" / "desktop"


def get_screenshot_dir() -> Path:
    raw = os.getenv(ENV_SCREENSHOT_DIR, "").strip()
    if raw:
        return Path(raw).expanduser()
    return get_desktop_base_dir() / "screenshots"


def get_template_store_path() -> Path:
    raw = os.getenv(ENV_TEMPLATE_STORE_PATH, "").strip()
    if raw:
        return Path(raw).expanduser()
    return get_desktop_base_dir() / "learning" / "learned_templates.json"

