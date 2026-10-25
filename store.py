"""Small JSON file that remembers things across restarts (which notes are done, when the roundup last ran)."""

from __future__ import annotations

import json
import os
from pathlib import Path

PATH = Path(__file__).parent / "state.json"


def load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(state: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, PATH)
