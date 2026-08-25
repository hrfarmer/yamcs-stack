"""Generate Yamcs runtime configuration from an exported firmware bundle."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from proves_yamcs.bundle import RuntimeBundle, load_bundle

EXPECTED_SPACE_SYSTEM = "ReferenceDeployment_ReferenceDeployment"
EXPECTED_ROOT_CONTAINER = "CCSDSSpacePacket"
GROUND_XTCE_NAME = "ground-control.xtce.xml"


def _atomic_text(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.chmod(mode)
    os.replace(temporary, path)


def _server_secret(runtime_dir: Path) -> str:
    path = runtime_dir / "secrets" / "yamcs-secret-key"
    if path.exists():
        value = path.read_text(encoding="ascii").strip()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError(f"invalid persisted Yamcs secret: {path}")
        return value
    value = secrets.token_hex(32)
    _atomic_text(path, f"{value}\n", 0o600)
    return value


def render_configuration(
    bundle: RuntimeBundle,
    template_dir: Path,
    runtime_dir: Path,
    *,
    ground_xtce_source: Path | None = None,
) -> Path:
    """Render all server configuration and return its root directory."""
    config_dir = runtime_dir / "config"
    etc_dir = config_dir / "etc"
    mdb_dir = config_dir / "mdb"
    etc_dir.mkdir(parents=True, exist_ok=True)
    mdb_dir.mkdir(parents=True, exist_ok=True)
    secret = _server_secret(runtime_dir)

    global_template = (template_dir / "yamcs.yaml.template").read_text(encoding="utf-8")
    _atomic_text(
        etc_dir / "yamcs.yaml",
        global_template.replace("@YAMCS_SECRET_KEY@", secret),
    )

    instance_template = (template_dir / "yamcs.fprime-project.yaml.template").read_text(
        encoding="utf-8"
    )
    instance_config = instance_template.replace(
        "@SPACECRAFT_ID@", str(bundle.spacecraft_id)
    ).replace("@FRAME_LENGTH@", str(bundle.frame_length))
    _atomic_text(etc_dir / "yamcs.fprime-project.yaml", instance_config)
    shutil.copyfile(template_dir / "processor.yaml", etc_dir / "processor.yaml")

    source = ground_xtce_source or (
        Path(__file__).resolve().parents[2] / "config" / "mdb" / GROUND_XTCE_NAME
    )
    shutil.copyfile(source, mdb_dir / GROUND_XTCE_NAME)
    return config_dir


def generate_xtce(bundle: RuntimeBundle, config_dir: Path) -> Path:
    output = config_dir / "mdb" / "fprime.xtce.xml"
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["fprime-to-xtce", str(bundle.dictionary_path), "-o", str(output)],
        check=True,
    )
    validate_xtce(output)
    return output


def validate_xtce(path: Path) -> None:
    root = ET.parse(path).getroot()
    if root.attrib.get("name") != EXPECTED_SPACE_SYSTEM:
        raise ValueError(
            f"XTCE root space system must be {EXPECTED_SPACE_SYSTEM!r}; "
            f"got {root.attrib.get('name')!r}"
        )
    containers = {
        element.attrib.get("name")
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "SequenceContainer"
    }
    if EXPECTED_ROOT_CONTAINER not in containers:
        raise ValueError(
            f"XTCE does not contain required root container {EXPECTED_ROOT_CONTAINER!r}"
        )


def prepare(input_dir: Path, runtime_dir: Path, template_dir: Path) -> RuntimeBundle:
    bundle = load_bundle(input_dir, require_auth_key=False)
    for directory in ("data", "cache", "pids", "state"):
        (runtime_dir / directory).mkdir(parents=True, exist_ok=True)
    config_dir = render_configuration(bundle, template_dir, runtime_dir)
    generate_xtce(bundle, config_dir)
    print(
        "Prepared Yamcs configuration "
        f"(spacecraft ID {bundle.spacecraft_id}, frame length {bundle.frame_length}) "
        f"at {config_dir}"
    )
    return bundle


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("inputs/proves"))
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--template-dir", type=Path, default=Path("config/etc"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    prepare(args.input_dir, args.runtime_dir, args.template_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
