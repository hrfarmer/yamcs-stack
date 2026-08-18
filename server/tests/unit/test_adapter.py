import pytest

from proves_yamcs.adapter import (
    TMFrameScanner,
    build_parser,
    crc16_ccitt,
    extract_space_packet,
    fix_ccsds_primary_header,
)


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


def test_adapter_parser_preserves_serial_and_tcp_modes():
    parser = build_parser()

    assert parser.parse_args(["--mode", "serial"]).mode == "serial"
    assert parser.parse_args(["--mode", "tcp"]).mode == "tcp"
