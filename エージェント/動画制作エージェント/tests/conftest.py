from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


TEST_DIR = Path(__file__).resolve().parent
AGENT_ROOT = TEST_DIR.parent
LIB_ROOT = AGENT_ROOT / "lib"
SCRIPTS_ROOT = AGENT_ROOT / "scripts"

if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
