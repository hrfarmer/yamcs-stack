"""Load and validate the multi-deployment Yamcs manifest."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from proves_yamcs.bundle import RuntimeBundle, load_bundle

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
DEFAULT_TM_PORT_BASE = 50000
DEFAULT_TC_PORT = 50001
MANIFEST_FILENAME = "deployments.json"


class DeploymentError(ValueError):
    """Raised when a deployments file or runtime manifest is invalid."""


@dataclass(frozen=True)
class DeploymentSpec:
    """One deployment as declared in deployments.toml."""

    name: str
    input_dir: Path
    tm_port: int
    tc_port: int


@dataclass(frozen=True)
class ResolvedDeployment:
    """A deployment after the firmware bundle has been loaded."""

    name: str
    bundle: RuntimeBundle
    tm_port: int
    tc_port: int

    @property
    def instance(self) -> str:
        return self.name

    @property
    def mdb_file(self) -> str:
        return f"{self.name}.xtce.xml"

    @property
    def spacecraft_id(self) -> int:
        return self.bundle.spacecraft_id

    @property
    def frame_length(self) -> int:
        return self.bundle.frame_length


@dataclass(frozen=True)
class RuntimeDeployment:
    """JSON-serializable deployment record written by prepare."""

    name: str
    instance: str
    spacecraft_id: int
    frame_length: int
    tm_port: int
    tc_port: int
    dictionary: str
    mdb_file: str


@dataclass(frozen=True)
class RuntimeManifest:
    """Runtime contract between prepare, supervisor, and gateway."""

    deployments: tuple[RuntimeDeployment, ...]

    def by_spacecraft_id(self) -> dict[int, RuntimeDeployment]:
        return {item.spacecraft_id: item for item in self.deployments}

    def as_dict(self) -> dict[str, Any]:
        return {"deployments": [asdict(item) for item in self.deployments]}


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeploymentError(f"{field} must be an integer")
    return value


def _as_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"{field} must be a non-empty string")
    return value.strip()


def _resolve_path(value: Any, base_dir: Path, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"{field} must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _validate_name(name: str) -> str:
    if NAME_PATTERN.fullmatch(name) is None:
        raise DeploymentError(
            f"deployment name {name!r} must match {NAME_PATTERN.pattern}"
        )
    return name


def _validate_port(value: int, field: str) -> int:
    if not 1 <= value <= 65535:
        raise DeploymentError(f"{field} must be in 1..65535")
    return value


def _unique(values: Sequence[Any], label: str) -> None:
    seen: dict[Any, int] = {}
    for value in values:
        seen[value] = seen.get(value, 0) + 1
    duplicates = sorted(str(value) for value, count in seen.items() if count > 1)
    if duplicates:
        raise DeploymentError(f"duplicate {label}: {', '.join(duplicates)}")


def load_deployment_specs(path: Path) -> tuple[DeploymentSpec, ...]:
    """Parse deployments.toml into ordered specs (bundles not yet loaded)."""
    deployments_path = path.expanduser().resolve()
    if not deployments_path.is_file():
        raise DeploymentError(f"deployments file not found: {deployments_path}")
    try:
        raw = tomllib.loads(deployments_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise DeploymentError(f"invalid TOML in {deployments_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise DeploymentError("deployments file root must be a table")
    unknown = sorted(set(raw) - {"deployment"})
    if unknown:
        raise DeploymentError(f"unknown deployments keys: {', '.join(unknown)}")
    entries = raw.get("deployment")
    if not isinstance(entries, list) or not entries:
        raise DeploymentError("at least one [[deployment]] table is required")

    specs: list[DeploymentSpec] = []
    base_dir = deployments_path.parent
    known_keys = {"name", "input_dir", "tm_port", "tc_port"}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise DeploymentError(f"deployment {index} must be a table")
        extra = sorted(set(entry) - known_keys)
        if extra:
            raise DeploymentError(
                f"unknown keys in deployment {index}: {', '.join(extra)}"
            )
        name = _validate_name(_as_str(entry.get("name"), "name"))
        input_dir = _resolve_path(entry.get("input_dir"), base_dir, "input_dir")
        tm_port = _validate_port(
            _as_int(
                entry.get("tm_port", DEFAULT_TM_PORT_BASE + 2 * index),
                "tm_port",
            ),
            "tm_port",
        )
        tc_port = _validate_port(
            _as_int(entry.get("tc_port", DEFAULT_TC_PORT), "tc_port"),
            "tc_port",
        )
        specs.append(
            DeploymentSpec(
                name=name,
                input_dir=input_dir,
                tm_port=tm_port,
                tc_port=tc_port,
            )
        )

    _unique([item.name for item in specs], "deployment name")
    _unique([item.tm_port for item in specs], "tm_port")
    return tuple(specs)


def resolve_deployments(
    specs: Sequence[DeploymentSpec],
) -> tuple[ResolvedDeployment, ...]:
    """Load firmware bundles and reject colliding spacecraft IDs."""
    resolved: list[ResolvedDeployment] = []
    for spec in specs:
        bundle = load_bundle(spec.input_dir, require_auth_key=False)
        resolved.append(
            ResolvedDeployment(
                name=spec.name,
                bundle=bundle,
                tm_port=spec.tm_port,
                tc_port=spec.tc_port,
            )
        )
    _unique([item.spacecraft_id for item in resolved], "spacecraft ID")
    return tuple(resolved)


def runtime_deployment(item: ResolvedDeployment) -> RuntimeDeployment:
    return RuntimeDeployment(
        name=item.name,
        instance=item.instance,
        spacecraft_id=item.spacecraft_id,
        frame_length=item.frame_length,
        tm_port=item.tm_port,
        tc_port=item.tc_port,
        dictionary=str(item.bundle.dictionary_path),
        mdb_file=item.mdb_file,
    )


def write_runtime_manifest(
    deployments: Sequence[ResolvedDeployment], path: Path
) -> RuntimeManifest:
    manifest = RuntimeManifest(
        deployments=tuple(runtime_deployment(item) for item in deployments)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.as_dict(), indent=2) + "\n", encoding="utf-8")
    return manifest


def load_runtime_manifest(path: Path) -> RuntimeManifest:
    """Load the JSON manifest written by prepare."""
    manifest_path = path.expanduser().resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeploymentError(f"runtime manifest not found: {manifest_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError(
            f"unable to load runtime manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise DeploymentError("runtime manifest root must be an object")
    entries = raw.get("deployments")
    if not isinstance(entries, list) or not entries:
        raise DeploymentError("runtime manifest must list at least one deployment")
    deployments: list[RuntimeDeployment] = []
    required = {
        "name",
        "instance",
        "spacecraft_id",
        "frame_length",
        "tm_port",
        "tc_port",
        "dictionary",
        "mdb_file",
    }
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise DeploymentError(f"manifest deployment {index} must be an object")
        missing = sorted(required - set(entry))
        if missing:
            raise DeploymentError(
                f"manifest deployment {index} missing: {', '.join(missing)}"
            )
        deployments.append(
            RuntimeDeployment(
                name=_as_str(entry["name"], "name"),
                instance=_as_str(entry["instance"], "instance"),
                spacecraft_id=_as_int(entry["spacecraft_id"], "spacecraft_id"),
                frame_length=_as_int(entry["frame_length"], "frame_length"),
                tm_port=_as_int(entry["tm_port"], "tm_port"),
                tc_port=_as_int(entry["tc_port"], "tc_port"),
                dictionary=_as_str(entry["dictionary"], "dictionary"),
                mdb_file=_as_str(entry["mdb_file"], "mdb_file"),
            )
        )
    _unique([item.name for item in deployments], "deployment name")
    _unique([item.spacecraft_id for item in deployments], "spacecraft ID")
    _unique([item.tm_port for item in deployments], "tm_port")
    return RuntimeManifest(deployments=tuple(deployments))
