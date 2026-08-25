from pathlib import Path

import pytest

from proves_yamcs.bundle import RuntimeBundle, load_bundle
from proves_yamcs.prepare import generate_xtce, render_configuration, validate_xtce


def bundle(tmp_path: Path) -> RuntimeBundle:
    return RuntimeBundle(
        directory=tmp_path,
        dictionary_path=tmp_path / "fprime-dictionary.json",
        spacecraft_id=68,
        frame_length=248,
    )


def test_render_configuration_substitutes_all_dynamic_values(tmp_path):
    runtime = tmp_path / "runtime"
    config = render_configuration(
        bundle(tmp_path),
        Path("config/etc"),
        runtime,
        tm_root_container="/ReferenceDeployment_ReferenceDeployment/CCSDSSpacePacket",
    )

    instance = (config / "etc/yamcs.fprime-project.yaml").read_text(encoding="utf-8")
    assert "@SPACECRAFT_ID@" not in instance
    assert "@FRAME_LENGTH@" not in instance
    assert "@TM_ROOT_CONTAINER@" not in instance
    assert instance.count("spacecraftId: 68") == 2
    assert instance.count("248") == 3
    assert "/ReferenceDeployment_ReferenceDeployment/CCSDSSpacePacket" in instance
    assert (config / "mdb/ground-control.xtce.xml").is_file()

    secret_file = runtime / "secrets/yamcs-secret-key"
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert secret_file.read_text(encoding="ascii").strip() not in Path(
        "config/etc/yamcs.yaml.template"
    ).read_text(encoding="utf-8")


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
        load_bundle(Path("tests/fixtures/proves")), tmp_path / "config"
    )

    assert generated.is_file()
    validate_xtce(generated)
