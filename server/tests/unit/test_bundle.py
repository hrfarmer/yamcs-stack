from pathlib import Path

import pytest

from proves_yamcs.bundle import BundleError, load_auth_key, load_bundle

FIXTURE = Path("tests/fixtures/proves")


def test_bundle_derives_firmware_constants():
    bundle = load_bundle(FIXTURE)

    assert bundle.spacecraft_id == 68
    assert bundle.frame_length == 248
    assert bundle.auth_key == "000102030405060708090a0b0c0d0e0f"


@pytest.mark.parametrize(
    "value",
    ["", "abc", "z" * 32, "00" * 17, "0x" + "00" * 16],
)
def test_auth_key_rejects_noncanonical_values(tmp_path, value):
    key_file = tmp_path / "auth-key.hex"
    key_file.write_text(value, encoding="utf-8")

    with pytest.raises(BundleError, match="32 hexadecimal"):
        load_auth_key(key_file)


def test_bundle_rejects_missing_dictionary(tmp_path):
    (tmp_path / "auth-key.hex").write_text("00" * 16, encoding="utf-8")

    with pytest.raises(BundleError, match="dictionary is missing"):
        load_bundle(tmp_path)
