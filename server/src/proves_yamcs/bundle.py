"""Validation and loading for a PROVES Yamcs runtime bundle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

AUTH_KEY_PATTERN = re.compile(r"[0-9a-fA-F]{32}")
DICTIONARY_FILENAME = "fprime-dictionary.json"
AUTH_KEY_FILENAME = "auth-key.hex"


class BundleError(ValueError):
    """Raised when an input bundle is absent or malformed."""


@dataclass(frozen=True)
class RuntimeBundle:
    """Validated paths and values needed by the server and adapter."""

    directory: Path
    dictionary_path: Path
    auth_key_path: Path
    auth_key: str
    spacecraft_id: int
    frame_length: int


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise BundleError(f"{name} must be an integer, not a boolean")
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise BundleError(f"{name} must be an integer; got {value!r}") from exc


def _constant(dictionary: dict[str, Any], qualified_name: str) -> int:
    matches = [
        entry.get("value")
        for entry in dictionary.get("constants", [])
        if entry.get("qualifiedName") == qualified_name
    ]
    if len(matches) != 1:
        raise BundleError(
            f"dictionary must contain exactly one {qualified_name}; "
            f"found {len(matches)}"
        )
    return _integer(matches[0], qualified_name)


def load_auth_key(path: Path) -> str:
    """Read a normalized 16-byte hex key without ever logging its value."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BundleError(
            f"unable to read authentication key file {path}: {exc}"
        ) from exc
    if AUTH_KEY_PATTERN.fullmatch(value) is None:
        raise BundleError(
            f"authentication key file {path} must contain exactly "
            "32 hexadecimal characters"
        )
    return value.lower()


def load_bundle(input_dir: str | Path) -> RuntimeBundle:
    """Load and validate the stable two-file input contract."""
    directory = Path(input_dir).expanduser().resolve()
    dictionary_path = directory / DICTIONARY_FILENAME
    auth_key_path = directory / AUTH_KEY_FILENAME

    if not directory.is_dir():
        raise BundleError(f"input directory does not exist: {directory}")
    try:
        dictionary = json.loads(dictionary_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BundleError(f"dictionary is missing: {dictionary_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(
            f"unable to load dictionary {dictionary_path}: {exc}"
        ) from exc
    if not isinstance(dictionary, dict):
        raise BundleError(f"dictionary root must be an object: {dictionary_path}")

    spacecraft_id = _constant(dictionary, "ComCfg.SpacecraftId")
    frame_length = _constant(dictionary, "ComCfg.TmFrameFixedSize")
    if not 0 <= spacecraft_id <= 0x3FF:
        raise BundleError(
            f"ComCfg.SpacecraftId must fit the CCSDS 10-bit field: {spacecraft_id}"
        )
    if frame_length < 8:
        raise BundleError(f"ComCfg.TmFrameFixedSize is too small: {frame_length}")

    return RuntimeBundle(
        directory=directory,
        dictionary_path=dictionary_path,
        auth_key_path=auth_key_path,
        auth_key=load_auth_key(auth_key_path),
        spacecraft_id=spacecraft_id,
        frame_length=frame_length,
    )
