"""Shared helpers for server unit tests."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path("tests/fixtures/proves")


def write_bundle(destination: Path, spacecraft_id: int) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    dictionary = json.loads(
        (FIXTURE / "fprime-dictionary.json").read_text(encoding="utf-8")
    )
    matches = 0
    for entry in dictionary.get("constants", []):
        if entry.get("qualifiedName") == "ComCfg.SpacecraftId":
            entry["value"] = spacecraft_id
            matches += 1
    assert matches == 1
    (destination / "fprime-dictionary.json").write_text(
        json.dumps(dictionary), encoding="utf-8"
    )
    return destination


def write_deployments(path: Path, entries: list[str]) -> Path:
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return path
