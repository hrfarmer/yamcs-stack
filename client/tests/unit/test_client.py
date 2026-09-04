import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from proves_gs.authentication import AuthenticateFramer, SequenceStore
from proves_gs.bundle import BundleError, load_auth_key, load_bundle
from proves_gs.client import (
    SatelliteRuntime,
    TMFrameScanner,
    build_parser,
    crc16_ccitt,
    extract_space_packet,
    extract_tc_spacecraft_id,
    fix_ccsds_primary_header,
    select_satellite_for_tc,
)
from proves_gs.config import ConfigError, apply_overrides, load_config
from proves_gs.radio import (
    FW_PACKET_COMMAND,
    GRC_BANDWIDTH_ENUM,
    GRC_CODING_RATE_ENUM,
    GRC_DEFAULT_OPCODES,
    RADIO_CIRCUITPYTHON,
    SPACE_PACKET_HEADER_SIZE,
    SPACE_PACKET_TYPE_TC,
    RadioController,
    RadioError,
    encode_circuitpython_settings,
    encode_fprime_ccsds_command,
    encode_grc_settings,
    load_grc_opcodes,
    validate_radio_settings,
)

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


def make_tc_frame(
    spacecraft_id: int, payload: bytes = b"\x18\x00\xc0\x00\x00\x00\xaa"
) -> bytes:
    header = (spacecraft_id & 0x3FF).to_bytes(2, "big") + b"\x00\x00\x00"
    return header + payload + b"zz"


def test_crc_known_vector():
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_scanner_recovers_valid_frames_from_junk_and_tracks_gaps():
    scanner = TMFrameScanner({68: 16})
    first = make_tm_frame(68, 3)
    second = make_tm_frame(68, 6)

    assert scanner.feed(b"console noise" + first[:7]) == []
    assert scanner.feed(first[7:] + b"junk" + second) == [first, second]
    assert scanner.frame_gaps == 2
    assert scanner.junk_bytes >= len(b"console noisejunk")


def test_scanner_accepts_multiple_spacecraft_ids():
    scanner = TMFrameScanner({68: 16, 67: 16})
    frames = [make_tm_frame(67, 1), make_tm_frame(68, 1)]

    assert scanner.feed(b"".join(frames)) == frames


def test_scanner_uses_per_scid_frame_length():
    scanner = TMFrameScanner({68: 16, 67: 20})
    first = make_tm_frame(68, 1, 16)
    second = make_tm_frame(67, 1, 20)

    assert scanner.feed(first + second) == [first, second]


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


def test_extract_tc_spacecraft_id():
    assert extract_tc_spacecraft_id(make_tc_frame(68)) == 68
    assert extract_tc_spacecraft_id(make_tc_frame(67)) == 67
    with pytest.raises(ValueError, match="spacecraft ID"):
        extract_tc_spacecraft_id(b"\x00")


def test_select_satellite_for_tc_matches_scid_and_drops_unknown():
    known = SatelliteRuntime(
        name="sat-a",
        spacecraft_id=68,
        frame_length=248,
        auth_framer=None,
        data_link_framer=MagicMock(),
    )
    satellites = {68: known}
    assert select_satellite_for_tc(make_tc_frame(68), satellites) is known
    assert select_satellite_for_tc(make_tc_frame(67), satellites) is None


def test_client_parser_requires_config():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    assert parser.parse_args(["--config", "config/gs.toml"]).config == Path(
        "config/gs.toml"
    )


def test_load_config_requires_satellite_tables(tmp_path):
    config_path = tmp_path / "gs.toml"
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    config_path.write_text(
        "\n".join(
            [
                'mode = "tcp"',
                'server_host = "yamcs.tailnet"',
                'station_name = "gs-a"',
                'tcp_host = "127.0.0.1"',
                "tcp_port = 5000",
                "[[satellite]]",
                'name = "proves-flight"',
                'input_dir = "inputs"',
                "skip_auth = true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.mode == "tcp"
    assert len(config.satellites) == 1
    assert config.satellites[0].name == "proves-flight"
    assert config.satellites[0].input_dir == input_dir.resolve()
    assert config.satellites[0].skip_auth is True
    assert config.satellites[0].sequence_number_file.name == "sequence-proves-flight"

    updated = apply_overrides(config, {"station_name": "gs-b", "tcp_port": 6000})
    assert updated.station_name == "gs-b"
    assert updated.tcp_port == 6000
    assert updated.server_host == "yamcs.tailnet"


def test_load_config_rejects_missing_satellite_table(tmp_path):
    config_path = tmp_path / "gs.toml"
    config_path.write_text(
        'mode = "serial"\nstation_name = "gs-a"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"\[\[satellite\]\]"):
        load_config(config_path)


def test_load_config_rejects_unknown_keys(tmp_path):
    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        'mode = "serial"\nstation_name = "x"\nbogus = 1\n'
        '[[satellite]]\nname = "a"\ninput_dir = "."\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown config keys"):
        load_config(config_path)


def test_load_config_accepts_dual_uart_and_radio_type(tmp_path):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    config_path = tmp_path / "gs.toml"
    config_path.write_text(
        "\n".join(
            [
                'mode = "serial"',
                'station_name = "gs-lab"',
                'uart_data_device = "/dev/ttyACM1"',
                'uart_control_device = "/dev/ttyACM0"',
                'radio_type = "circuit-python-passthrough"',
                "[[satellite]]",
                'name = "proves-flight"',
                'input_dir = "inputs"',
                "skip_auth = true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.uart_device == "/dev/ttyACM1"
    assert config.uart_control_device == "/dev/ttyACM0"
    assert config.radio_type == "circuit-python-passthrough"


def test_load_config_requires_control_port_for_radio(tmp_path):
    config_path = tmp_path / "gs.toml"
    config_path.write_text(
        "\n".join(
            [
                'mode = "serial"',
                'station_name = "gs-lab"',
                'uart_device = "/dev/ttyACM0"',
                'radio_type = "grc"',
                "[[satellite]]",
                'name = "a"',
                'input_dir = "."',
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="uart_control_device"):
        load_config(config_path)


def test_circuitpython_settings_are_console_tokens():
    assert encode_circuitpython_settings({"mode": "2"}) == b"2\n"
    with pytest.raises(RadioError, match="mode"):
        validate_radio_settings(RADIO_CIRCUITPYTHON, {"mode": "9"})


def _parse_grc_tc_command(frame: bytes) -> tuple[int, int, int, int, bytes]:
    """Return (tc_scid, apid, descriptor, opcode, args) from a GRC uplink frame."""
    assert crc16_ccitt(frame[:-2]) == int.from_bytes(frame[-2:], "big")
    tc_scid = int.from_bytes(frame[0:2], "big") & 0x3FF
    space_packet = frame[5:-2]
    identification = int.from_bytes(space_packet[0:2], "big")
    packet_type = (identification >> 12) & 0x1
    apid = identification & 0x7FF
    data_length = int.from_bytes(space_packet[4:6], "big") + 1
    user = space_packet[
        SPACE_PACKET_HEADER_SIZE : SPACE_PACKET_HEADER_SIZE + data_length
    ]
    assert packet_type == SPACE_PACKET_TYPE_TC
    assert len(user) == data_length
    descriptor = int.from_bytes(user[0:2], "big")
    opcode = int.from_bytes(user[2:6], "big")
    return tc_scid, apid, descriptor, opcode, user[6:]


def test_grc_frames_are_space_packets_with_params_before_set_freq():
    frequency = 437_425_000
    frames = encode_grc_settings(
        {
            "frequency_hz": frequency,
            "spreading_factor": 7,
            "bandwidth_tx_khz": 500,
            "bandwidth_rx_khz": 125,
            "coding_rate": 6,
        },
        scid=0x44,
        vcid=1,
    )
    assert len(frames) == 5
    parsed = [_parse_grc_tc_command(frame) for frame in frames]
    assert [item[3] for item in parsed] == [
        GRC_DEFAULT_OPCODES["DATA_RATE_PRM_SET"],
        GRC_DEFAULT_OPCODES["BANDWIDTH_TX_PRM_SET"],
        GRC_DEFAULT_OPCODES["BANDWIDTH_RX_PRM_SET"],
        GRC_DEFAULT_OPCODES["CODING_RATE_PRM_SET"],
        GRC_DEFAULT_OPCODES["SET_FREQ"],
    ]
    assert parsed[-1][4] == frequency.to_bytes(4, "big")
    assert parsed[0][4] == bytes([7])
    assert parsed[1][4] == bytes([GRC_BANDWIDTH_ENUM[500]])
    assert parsed[2][4] == bytes([GRC_BANDWIDTH_ENUM[125]])
    assert parsed[3][4] == bytes([GRC_CODING_RATE_ENUM[6]])
    for tc_scid, apid, descriptor, _opcode, _args in parsed:
        assert tc_scid == 0x44
        assert apid == FW_PACKET_COMMAND
        assert descriptor == FW_PACKET_COMMAND
    assert b"\x5a\x5a\x5a\x5a" not in b"".join(frames)


def test_grc_opcode_lookup_from_dictionary(tmp_path):
    dictionary = tmp_path / "dict.json"
    dictionary.write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "name": "ReferenceDeployment.uhf.SET_FREQ",
                        "opcode": 42,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_grc_opcodes(dictionary)["SET_FREQ"] == 42


def test_radio_controller_writes_circuitpython_mode():
    port = MagicMock()
    radio = RadioController(RADIO_CIRCUITPYTHON, port)
    applied = radio.apply({"mode": "U"})
    port.write.assert_called_once_with(b"U\n")
    assert applied == {"mode": "U"}
    assert radio.status()["type"] == RADIO_CIRCUITPYTHON


def test_encode_fprime_ccsds_command_is_crc_valid_tc():
    args = (437_400_000).to_bytes(4, "big")
    frame = encode_fprime_ccsds_command(
        0x10017009,
        args,
        scid=0x44,
        vcid=1,
        sequence_count=0,
    )
    tc_scid, apid, descriptor, opcode, decoded_args = _parse_grc_tc_command(frame)
    assert tc_scid == 0x44
    assert apid == FW_PACKET_COMMAND
    assert descriptor == FW_PACKET_COMMAND
    assert opcode == 0x10017009
    assert decoded_args == args


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
