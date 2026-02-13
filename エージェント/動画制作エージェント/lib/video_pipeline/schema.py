from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate as jsonschema_validate


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_with_schema(payload: dict, schema: dict) -> None:
    jsonschema_validate(instance=payload, schema=schema)
