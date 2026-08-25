"""Unit tests for CCSDS TC reassembly and spacecraft-ID rewrite in the bridge."""

from __future__ import annotations

import struct

from udp_tcp_bridge import (
    ccsds_tc_frame_length,
    crc16_ccitt,
    extract_tc_spacecraft_id,
    extract_tm_spacecraft_id,
    parse_spacecraft,
    pop_tc_frames,
    rewrite_tc,
    rewrite_tm,
    set_tc_spacecraft_id,
    set_tm_spacecraft_id,
)


def _tc_frame(payload: bytes, scid: int = 68, vcid: int = 1) -> bytes:
    length_minus_one = len(payload) + 5 + 2 - 1
    header1 = (1 << 13) | (scid & 0x3FF)
    header2 = ((vcid & 0x3F) << 10) | (length_minus_one & 0x3FF)
    header = struct.pack(">HHB", header1, header2, 0)
    body = header + payload
    crc = crc16_ccitt(body).to_bytes(2, "big")
    return body + crc


def _tm_frame(scid: int, length: int = 16, vc_id: int = 1) -> bytes:
    word = (scid << 4) | (vc_id << 1)
    frame = bytearray(length)
    frame[0] = (word >> 8) & 0xFF
    frame[1] = word & 0xFF
    frame[-2:] = crc16_ccitt(bytes(frame[:-2])).to_bytes(2, "big")
    return bytes(frame)


def test_ccsds_tc_frame_length_reads_header():
    frame = _tc_frame(b"\x18\x00\xc0\x00\x00\x00\xaa")
    assert ccsds_tc_frame_length(frame) == len(frame)
    assert ccsds_tc_frame_length(frame[:3]) is None


def test_pop_tc_frames_splits_stream_and_handles_partial():
    first = _tc_frame(b"\x01\x02")
    second = _tc_frame(b"\x03\x04\x05")
    buffer = bytearray(first + second[:4])
    assert pop_tc_frames(buffer) == [first]
    assert bytes(buffer) == second[:4]
    buffer.extend(second[4:])
    assert pop_tc_frames(buffer) == [second]
    assert buffer == bytearray()


def test_set_tm_spacecraft_id_updates_header_and_crc():
    original = _tm_frame(68)
    rewritten = set_tm_spacecraft_id(original, 67)
    assert extract_tm_spacecraft_id(rewritten) == 67
    assert rewritten[-2:] == crc16_ccitt(rewritten[:-2]).to_bytes(2, "big")
    assert original[2:-2] == rewritten[2:-2]


def test_set_tc_spacecraft_id_preserves_flags():
    original = _tc_frame(b"\x18\x00", scid=67)
    rewritten = set_tc_spacecraft_id(original, 68)
    assert extract_tc_spacecraft_id(rewritten) == 68
    assert (rewritten[0] & 0xFC) == (original[0] & 0xFC)
    assert rewritten[-2:] == crc16_ccitt(rewritten[:-2]).to_bytes(2, "big")


def test_rewrite_helpers_are_noops_when_wire_scid_matches_native():
    link = parse_spacecraft("52000:52001", native_scid=68, tc_host="127.0.0.1")
    frame = _tm_frame(68)
    assert rewrite_tm(frame, link) is frame
    tc = _tc_frame(b"\x01")
    assert rewrite_tc(tc, link) is tc


def test_parse_spacecraft_rewrites_when_wire_scid_differs():
    link = parse_spacecraft("52010:52011:67", native_scid=68, tc_host="127.0.0.1")
    assert link.rewrite
    assert link.wire_scid == 67
    tm = rewrite_tm(_tm_frame(68), link)
    assert extract_tm_spacecraft_id(tm) == 67
    tc = rewrite_tc(_tc_frame(b"\x02", scid=67), link)
    assert extract_tc_spacecraft_id(tc) == 68
