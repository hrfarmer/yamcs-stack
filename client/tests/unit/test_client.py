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


def test_client_parser_defaults_to_gateway_tm_port():
    args = build_parser().parse_args([])
    assert args.server_tm_port == 51000
    assert args.mode == "serial"


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
