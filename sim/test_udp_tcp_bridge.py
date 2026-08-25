"""Unit tests for CCSDS TC reassembly in the UDP/TCP bridge."""

from __future__ import annotations

import struct

from udp_tcp_bridge import ccsds_tc_frame_length, pop_tc_frames


def _tc_frame(payload: bytes, scid: int = 68, vcid: int = 1) -> bytes:
    length_minus_one = len(payload) + 5 + 2 - 1
    header1 = (1 << 13) | (scid & 0x3FF)
    header2 = ((vcid & 0x3F) << 10) | (length_minus_one & 0x3FF)
    header = struct.pack(">HHB", header1, header2, 0)
    body = header + payload
    # CRC placeholder; length parsing does not validate FECF.
    return body + b"\x00\x00"


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
