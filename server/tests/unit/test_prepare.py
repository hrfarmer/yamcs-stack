import json
from pathlib import Path

import pytest

from proves_yamcs.bundle import RuntimeBundle, load_bundle
from proves_yamcs.deployments import ResolvedDeployment
from proves_yamcs.prepare import (
    generate_xtce,
    prepare,
    render_configuration,
    validate_xtce,
)
from tests.unit.helpers import write_bundle, write_deployments


def bundle(tmp_path: Path, spacecraft_id: int = 68) -> RuntimeBundle:
    return RuntimeBundle(
        directory=tmp_path,
        dictionary_path=tmp_path / "fprime-dictionary.json",
        spacecraft_id=spacecraft_id,
        frame_length=248,
    )


def resolved(
    tmp_path: Path,
    *,
    name: str = "proves-flight",
    spacecraft_id: int = 68,
    tm_port: int = 50000,
) -> ResolvedDeployment:
    return ResolvedDeployment(
        name=name,
        bundle=bundle(tmp_path, spacecraft_id),
        tm_port=tm_port,
        tc_port=50001,
    )


def test_render_configuration_substitutes_all_dynamic_values(tmp_path):
    runtime = tmp_path / "runtime"
    item = resolved(tmp_path)
    config = render_configuration(
        [item],
        Path("config/etc"),
        runtime,
        tm_root_containers={
            "proves-flight": "/ReferenceDeployment_ReferenceDeployment/CCSDSSpacePacket"
        },
    )

    instance = (config / "etc/yamcs.proves-flight.yaml").read_text(encoding="utf-8")
    assert "@SPACECRAFT_ID@" not in instance
    assert "@FRAME_LENGTH@" not in instance
    assert "@TM_ROOT_CONTAINER@" not in instance
    assert "@TM_PORT@" not in instance
    assert "@MDB_FILE@" not in instance
    assert instance.count("spacecraftId: 68") == 2
    assert instance.count("248") == 3
    assert "port: 50000" in instance
    assert "proves-flight.xtce.xml" in instance
    assert "/ReferenceDeployment_ReferenceDeployment/CCSDSSpacePacket" in instance
    assert (config / "mdb/ground-control.xtce.xml").is_file()
    global_config = (config / "etc/yamcs.yaml").read_text(encoding="utf-8")
    assert "proves-flight" in global_config
    assert "@INSTANCES@" not in global_config
    overlay = (runtime / "compose.udp.yaml").read_text(encoding="utf-8")
    assert "127.0.0.1:50000:50000/udp" in overlay
    assert "dashboards/json/proves-flight/overview.json" in overlay
    manifest = (config / "deployments.json").read_text(encoding="utf-8")
    assert "proves-flight" in manifest
    datasource = (runtime / "grafana/datasources/yamcs.yaml").read_text(
        encoding="utf-8"
    )
    assert '"proves-flight_realtime"' in datasource
    overview = json.loads(
        (runtime / "grafana/dashboards/proves-flight/overview.json").read_text(
            encoding="utf-8"
        )
    )
    assert overview["uid"] == "yamcs-proves-flight-overview"
    assert overview["title"] == "proves-flight Overview"

    secret_file = runtime / "secrets/yamcs-secret-key"
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert secret_file.read_text(encoding="ascii").strip() not in Path(
        "config/etc/yamcs.yaml.template"
    ).read_text(encoding="utf-8")


def test_render_configuration_writes_two_instances(tmp_path):
    runtime = tmp_path / "runtime"
    deployments = [
        resolved(tmp_path / "a", name="sat-a", spacecraft_id=68, tm_port=50000),
        resolved(tmp_path / "b", name="sat-b", spacecraft_id=67, tm_port=50002),
    ]
    config = render_configuration(deployments, Path("config/etc"), runtime)
    global_config = (config / "etc/yamcs.yaml").read_text(encoding="utf-8")
    assert "- sat-a" in global_config
    assert "- sat-b" in global_config
    assert (config / "etc/yamcs.sat-a.yaml").is_file()
    assert (config / "etc/yamcs.sat-b.yaml").is_file()
    overlay = (runtime / "compose.udp.yaml").read_text(encoding="utf-8")
    assert "50000:50000/udp" in overlay
    assert "50002:50002/udp" in overlay
    datasource = (runtime / "grafana/datasources/yamcs.yaml").read_text(
        encoding="utf-8"
    )
    assert '"sat-a_realtime"' in datasource
    assert '"sat-b_replay"' in datasource
    sat_a = json.loads(
        (runtime / "grafana/dashboards/sat-a/overview.json").read_text(encoding="utf-8")
    )
    sat_b = json.loads(
        (runtime / "grafana/dashboards/sat-b/commanding.json").read_text(
            encoding="utf-8"
        )
    )
    assert sat_a["uid"] == "yamcs-sat-a-overview"
    assert sat_b["uid"] == "yamcs-sat-b-commanding"
    assert "sat-a_realtime" in json.dumps(sat_a)
    assert "sat-b_realtime" in json.dumps(sat_b)
    assert "sat-a_realtime" not in json.dumps(sat_b)


def test_validate_xtce_requires_root_container(tmp_path):
    valid = tmp_path / "valid.xml"
    valid.write_text(
        '<SpaceSystem name="ReferenceDeployment_ReferenceDeployment">'
        '<SequenceContainer name="CCSDSSpacePacket"/>'
        "</SpaceSystem>",
        encoding="utf-8",
    )
    validate_xtce(valid)

    invalid = tmp_path / "invalid.xml"
    invalid.write_text('<SpaceSystem name="anything"/>', encoding="utf-8")
    with pytest.raises(ValueError, match="root container"):
        validate_xtce(invalid)


def test_generate_xtce_from_representative_dictionary(tmp_path):
    generated = generate_xtce(
        load_bundle(Path("tests/fixtures/proves")),
        tmp_path / "config",
        "proves-flight.xtce.xml",
    )

    assert generated.is_file()
    validate_xtce(generated)


def test_prepare_two_deployments(tmp_path):
    first = write_bundle(tmp_path / "sat-a", 68)
    second = write_bundle(tmp_path / "sat-b", 67)
    deployments_file = write_deployments(
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
    runtime = tmp_path / "runtime"
    prepared = prepare(deployments_file, runtime, Path("config/etc"))
    assert [item.name for item in prepared] == ["sat-a", "sat-b"]
    config = runtime / "config"
    assert (config / "mdb/sat-a.xtce.xml").is_file()
    assert (config / "mdb/sat-b.xtce.xml").is_file()
    sat_a = (config / "etc/yamcs.sat-a.yaml").read_text(encoding="utf-8")
    sat_b = (config / "etc/yamcs.sat-b.yaml").read_text(encoding="utf-8")
    assert "spacecraftId: 68" in sat_a
    assert "spacecraftId: 67" in sat_b
    assert "/CCSDSSpacePacket" in sat_a
    assert "/CCSDSSpacePacket" in sat_b
    grafana_a = json.loads(
        (runtime / "grafana/dashboards/sat-a/overview.json").read_text(encoding="utf-8")
    )
    grafana_b = json.loads(
        (runtime / "grafana/dashboards/sat-b/overview.json").read_text(encoding="utf-8")
    )
    assert grafana_a["title"] == "sat-a Overview"
    assert grafana_b["title"] == "sat-b Overview"
    assert (runtime / "grafana/dashboards/sat-a/commanding.json").is_file()
    assert (runtime / "grafana/dashboards/sat-b/commanding.json").is_file()
