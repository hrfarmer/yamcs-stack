from pathlib import Path

import pytest

from proves_yamcs.deployments import (
    DeploymentError,
    load_deployment_specs,
    load_runtime_manifest,
    resolve_deployments,
    write_runtime_manifest,
)
from tests.unit.helpers import FIXTURE, write_bundle, write_deployments


def test_fixture_deployments_file_points_at_proves_bundle():
    specs = load_deployment_specs(Path("tests/fixtures/deployments.toml"))
    assert len(specs) == 1
    assert specs[0].name == "proves-flight"
    assert specs[0].input_dir == FIXTURE.resolve()
    assert specs[0].tm_port == 50000
    assert specs[0].tc_port == 50001


def test_load_deployment_specs_assigns_ports_and_rejects_duplicates(tmp_path):
    first = write_bundle(tmp_path / "sat-a", 68)
    second = write_bundle(tmp_path / "sat-b", 67)
    deployments = write_deployments(
        tmp_path / "deployments.toml",
        [
            "[[deployment]]",
            'name = "sat-a"',
            f'input_dir = "{first}"',
            "[[deployment]]",
            'name = "sat-b"',
            f'input_dir = "{second}"',
        ],
    )
    specs = load_deployment_specs(deployments)
    assert [item.tm_port for item in specs] == [50000, 50002]
    resolved = resolve_deployments(specs)
    assert [item.spacecraft_id for item in resolved] == [68, 67]


def test_load_deployment_specs_rejects_empty_file(tmp_path):
    path = tmp_path / "empty.toml"
    path.write_text("# no deployments\n", encoding="utf-8")
    with pytest.raises(DeploymentError, match="at least one"):
        load_deployment_specs(path)


def test_load_deployment_specs_rejects_duplicate_names(tmp_path):
    bundle = write_bundle(tmp_path / "sat", 68)
    path = write_deployments(
        tmp_path / "deployments.toml",
        [
            "[[deployment]]",
            'name = "sat-a"',
            f'input_dir = "{bundle}"',
            "[[deployment]]",
            'name = "sat-a"',
            f'input_dir = "{bundle}"',
            "tm_port = 50002",
        ],
    )
    with pytest.raises(DeploymentError, match="duplicate deployment name"):
        load_deployment_specs(path)


def test_load_deployment_specs_rejects_duplicate_tm_ports(tmp_path):
    first = write_bundle(tmp_path / "sat-a", 68)
    second = write_bundle(tmp_path / "sat-b", 67)
    path = write_deployments(
        tmp_path / "deployments.toml",
        [
            "[[deployment]]",
            'name = "sat-a"',
            f'input_dir = "{first}"',
            "tm_port = 50000",
            "[[deployment]]",
            'name = "sat-b"',
            f'input_dir = "{second}"',
            "tm_port = 50000",
        ],
    )
    with pytest.raises(DeploymentError, match="duplicate tm_port"):
        load_deployment_specs(path)


def test_resolve_deployments_rejects_duplicate_spacecraft_ids(tmp_path):
    first = write_bundle(tmp_path / "sat-a", 68)
    second = write_bundle(tmp_path / "sat-b", 68)
    path = write_deployments(
        tmp_path / "deployments.toml",
        [
            "[[deployment]]",
            'name = "sat-a"',
            f'input_dir = "{first}"',
            "[[deployment]]",
            'name = "sat-b"',
            f'input_dir = "{second}"',
        ],
    )
    with pytest.raises(DeploymentError, match="duplicate spacecraft ID"):
        resolve_deployments(load_deployment_specs(path))


def test_runtime_manifest_round_trip(tmp_path):
    first = write_bundle(tmp_path / "sat-a", 68)
    specs = load_deployment_specs(
        write_deployments(
            tmp_path / "deployments.toml",
            [
                "[[deployment]]",
                'name = "sat-a"',
                f'input_dir = "{first}"',
            ],
        )
    )
    manifest_path = tmp_path / "deployments.json"
    written = write_runtime_manifest(resolve_deployments(specs), manifest_path)
    loaded = load_runtime_manifest(manifest_path)
    assert loaded == written
    assert loaded.deployments[0].spacecraft_id == 68
