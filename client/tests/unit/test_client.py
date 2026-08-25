from pathlib import Path

import pytest

from proves_gs.authentication import AuthenticateFramer, SequenceStore
from proves_gs.bundle import BundleError, load_auth_key, load_bundle
from proves_gs.client import (
    TMFrameScanner,
    build_parser,
    crc16_ccitt,
    extract_space_packet,
    fix_ccsds_primary_header,
)
from proves_gs.config import ConfigError, apply_overrides, load_config

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "proves"


def make_tm_frame(spacecraft_id: int, vc_count: int, length: int = 16) -> bytes:
    word = (spacecraft_id << 4) | (1 << 1)
    frame = bytearray(length)
    frame[0:2] = word.to_bytes(2, "big")
    frame[3] = vc_count
    for index in range(4, length - 2):
        frame[index] = index
    frame[-2:] = crc16_ccitt(frame[:-2]).to_bytes(2, "big")
    return bytes(frame)


def test_crc_known_vector():
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_scanner_recovers_valid_frames_from_junk_and_tracks_gaps():
    scanner = TMFrameScanner(16, [68])
    first = make_tm_frame(68, 3)
    second = make_tm_frame(68, 6)

    assert scanner.feed(b"console noise" + first[:7]) == []
    assert scanner.feed(first[7:] + b"junk" + second) == [first, second]
    assert scanner.frame_gaps == 2
    assert scanner.junk_bytes >= len(b"console noisejunk")


def test_scanner_accepts_multiple_spacecraft_ids():
    scanner = TMFrameScanner(16, [68, 67])
    frames = [make_tm_frame(67, 1), make_tm_frame(68, 1)]

    assert scanner.feed(b"".join(frames)) == frames


def test_extract_and_fix_space_packet_header():
    packet = bytes.fromhex("1800c0000000aabbcc")
    transfer_frame = b"12345" + packet + b"zz"
    extracted = extract_space_packet(transfer_frame)

    fix_ccsds_primary_header(extracted)

    assert extracted[4:6] == (len(packet) - 7).to_bytes(2, "big")
    assert extracted[2] & 0xC0 == 0xC0


def test_extract_rejects_short_tc_frame():
    with pytest.raises(ValueError, match="too short"):
        extract_space_packet(b"short")


def test_client_parser_requires_config():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    assert parser.parse_args(["--config", "config/gs.toml"]).config == Path(
        "config/gs.toml"
    )


def test_load_config_resolves_paths_and_overrides(tmp_path):
    config_path = tmp_path / "gs.toml"
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    config_path.write_text(
        "\n".join(
            [
                'mode = "tcp"',
                'input_dir = "inputs"',
                'server_host = "yamcs.tailnet"',
                'station_name = "gs-a"',
                'tcp_host = "127.0.0.1"',
                "tcp_port = 5000",
                "skip_auth = true",
                "spacecraft_ids = [68, 67]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.mode == "tcp"
    assert config.input_dir == input_dir.resolve()
    assert config.spacecraft_ids == (68, 67)
    assert config.skip_auth is True

    updated = apply_overrides(config, {"station_name": "gs-b", "tcp_port": 6000})
    assert updated.station_name == "gs-b"
    assert updated.tcp_port == 6000
    assert updated.server_host == "yamcs.tailnet"


def test_load_config_rejects_unknown_keys(tmp_path):
    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        'mode = "serial"\nstation_name = "x"\nbogus = 1\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown config keys"):
        load_config(config_path)


def test_authentication_known_vector_and_persistent_sequence(tmp_path):
    state = tmp_path / "sequence"
    framer = AuthenticateFramer("000102030405060708090a0b0c0d0e0f", state, spi=0)
    first = framer.frame(b"\x01\x02")
    second = framer.frame(b"\x01\x02")
    assert first != second
    assert SequenceStore(state)._read() == 2

    restarted = AuthenticateFramer("000102030405060708090a0b0c0d0e0f", state, spi=0)
    third = restarted.frame(b"\x01\x02")
    assert third[2:6] == (2).to_bytes(4, "big")


@pytest.mark.parametrize(
    "value",
    ["", "abc", "z" * 32, "00" * 17, "0x" + "00" * 16],
)
def test_auth_key_rejects_noncanonical_values(tmp_path, value):
    key_file = tmp_path / "auth-key.hex"
    key_file.write_text(value, encoding="utf-8")

    with pytest.raises(BundleError, match="32 hexadecimal"):
        load_auth_key(key_file)


def test_bundle_derives_firmware_constants():
    bundle = load_bundle(FIXTURE)
    assert bundle.spacecraft_id == 68
    assert bundle.frame_length == 248


def test_bundle_rejects_missing_dictionary(tmp_path):
    (tmp_path / "auth-key.hex").write_text("00" * 16, encoding="utf-8")

    with pytest.raises(BundleError, match="dictionary is missing"):
        load_bundle(tmp_path)
