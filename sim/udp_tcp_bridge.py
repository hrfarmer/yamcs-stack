#!/usr/bin/env python3
"""Bridge F´ Drv.Udp CCSDS frames to the GS client's TCP bent-pipe.

F´ with ``-a HOST -p PORT`` sends TM to HOST:PORT and receives TC on HOST:PORT+1.
The GS client connects as a TCP client and expects a bidirectional byte stream.
TM frames from F´ are fixed-length and forwarded as-is (or with a rewritten
spacecraft ID). TC frames from the GS client are variable-length CCSDS TC
transfer frames; this process reassembles them from the TCP stream using the
length field in the TC primary header and routes each frame to the matching F´.
"""

from __future__ import annotations

import argparse
import dataclasses
import selectors
import socket
import sys

sys.stdout.reconfigure(line_buffering=True)

TC_HEADER_SIZE = 5
DEFAULT_NATIVE_SCID = 68


def crc16_ccitt(data: bytes) -> int:
    """CCSDS CRC16-CCITT (poly 0x1021, initial value 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


def ccsds_tc_frame_length(buffer: bytes | bytearray) -> int | None:
    """Return total TC frame length from a CCSDS TC header, or None if incomplete.

    The TC primary header encodes frame length as (total octets - 1) in the low
    10 bits of bytes 2-3 (VCID occupies the high 6 bits of byte 2).
    """
    if len(buffer) < 4:
        return None
    length_minus_one = ((buffer[2] & 0x03) << 8) | buffer[3]
    return length_minus_one + 1


def pop_tc_frames(buffer: bytearray) -> list[bytes]:
    """Extract complete CCSDS TC frames from a TCP reassembly buffer."""
    frames: list[bytes] = []
    while True:
        total = ccsds_tc_frame_length(buffer)
        if total is None:
            break
        if total < TC_HEADER_SIZE + 2:
            # Corrupt length; drop one byte and resync.
            del buffer[0:1]
            continue
        if len(buffer) < total:
            break
        frames.append(bytes(buffer[:total]))
        del buffer[:total]
    return frames


def extract_tm_spacecraft_id(frame: bytes) -> int:
    return ((frame[0] << 8) | frame[1]) >> 4 & 0x3FF


def extract_tc_spacecraft_id(frame: bytes) -> int:
    if len(frame) < 2:
        raise ValueError("TC frame too short to read spacecraft ID")
    return ((frame[0] << 8) | frame[1]) & 0x3FF


def set_tm_spacecraft_id(frame: bytes, spacecraft_id: int) -> bytes:
    """Rewrite the TM transfer-frame spacecraft ID and FECF."""
    if len(frame) < 4:
        return frame
    buf = bytearray(frame)
    word = (buf[0] << 8) | buf[1]
    buf[0] = (((spacecraft_id & 0x3FF) << 4) | (word & 0x0F)) >> 8
    buf[1] = (((spacecraft_id & 0x3FF) << 4) | (word & 0x0F)) & 0xFF
    crc = crc16_ccitt(bytes(buf[:-2]))
    buf[-2:] = crc.to_bytes(2, "big")
    return bytes(buf)


def set_tc_spacecraft_id(frame: bytes, spacecraft_id: int) -> bytes:
    """Rewrite the TC transfer-frame spacecraft ID and FECF."""
    if len(frame) < 4:
        return frame
    buf = bytearray(frame)
    word = (buf[0] << 8) | buf[1]
    updated = (word & ~0x3FF) | (spacecraft_id & 0x3FF)
    buf[0] = (updated >> 8) & 0xFF
    buf[1] = updated & 0xFF
    crc = crc16_ccitt(bytes(buf[:-2]))
    buf[-2:] = crc.to_bytes(2, "big")
    return bytes(buf)


@dataclasses.dataclass(frozen=True)
class SpacecraftLink:
    tm_port: int
    tc_host: str
    tc_port: int
    wire_scid: int
    native_scid: int

    @property
    def rewrite(self) -> bool:
        return self.wire_scid != self.native_scid


def parse_spacecraft(
    value: str, native_scid: int, tc_host: str
) -> SpacecraftLink:
    parts = value.split(":")
    if len(parts) not in {2, 3}:
        raise argparse.ArgumentTypeError(
            "expected TM:TC or TM:TC:WIRE_SCID, got " + value
        )
    try:
        tm_port = int(parts[0])
        tc_port = int(parts[1])
        wire_scid = int(parts[2]) if len(parts) == 3 else native_scid
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid spacecraft spec {value!r}") from exc
    return SpacecraftLink(
        tm_port=tm_port,
        tc_host=tc_host,
        tc_port=tc_port,
        wire_scid=wire_scid,
        native_scid=native_scid,
    )


def rewrite_tm(frame: bytes, link: SpacecraftLink) -> bytes:
    if not link.rewrite:
        return frame
    return set_tm_spacecraft_id(frame, link.wire_scid)


def rewrite_tc(frame: bytes, link: SpacecraftLink) -> bytes:
    if not link.rewrite:
        return frame
    return set_tc_spacecraft_id(frame, link.native_scid)


def _serve(
    tcp_host: str,
    tcp_port: int,
    links: list[SpacecraftLink],
) -> None:
    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_server.bind((tcp_host, tcp_port))
    tcp_server.listen(1)
    print(f"[bridge] waiting for GS client on tcp://{tcp_host}:{tcp_port}")

    tm_socks: dict[socket.socket, SpacecraftLink] = {}
    tc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    by_wire_scid = {link.wire_scid: link for link in links}
    for link in links:
        tm_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tm_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tm_sock.bind(("0.0.0.0", link.tm_port))
        tm_socks[tm_sock] = link
        rewrite = (
            f", rewrite SCID {link.native_scid}->{link.wire_scid}"
            if link.rewrite
            else ""
        )
        print(
            f"[bridge] F´ TM udp://0.0.0.0:{link.tm_port} -> TCP"
            f"{rewrite}; TCP TC SCID {link.wire_scid} -> "
            f"udp://{link.tc_host}:{link.tc_port}"
        )

    while True:
        client, addr = tcp_server.accept()
        print(f"[bridge] GS client connected from {addr[0]}:{addr[1]}")
        client.setblocking(False)
        selector = selectors.DefaultSelector()
        selector.register(client, selectors.EVENT_READ)
        for tm_sock in tm_socks:
            tm_sock.setblocking(False)
            selector.register(tm_sock, selectors.EVENT_READ)
        tc_buffer = bytearray()
        try:
            while True:
                for key, _ in selector.select(timeout=1.0):
                    if key.fileobj in tm_socks:
                        frame, _ = key.fileobj.recvfrom(65535)
                        client.sendall(rewrite_tm(frame, tm_socks[key.fileobj]))
                    else:
                        data = client.recv(65535)
                        if not data:
                            raise ConnectionError("GS client disconnected")
                        tc_buffer.extend(data)
                        for frame in pop_tc_frames(tc_buffer):
                            try:
                                wire_scid = extract_tc_spacecraft_id(frame)
                            except ValueError:
                                print("[bridge] dropping short TC frame")
                                continue
                            link = by_wire_scid.get(wire_scid)
                            if link is None:
                                print(
                                    f"[bridge] dropping TC for unknown SCID {wire_scid}"
                                )
                                continue
                            outbound = rewrite_tc(frame, link)
                            tc_sock.sendto(
                                outbound, (link.tc_host, link.tc_port)
                            )
                            print(
                                f"[bridge] TC {len(frame)} bytes SCID {wire_scid} -> "
                                f"{link.tc_host}:{link.tc_port}"
                            )
        except (OSError, ConnectionError) as exc:
            print(f"[bridge] session ended: {exc}")
        finally:
            selector.close()
            client.close()
            print("[bridge] waiting for next GS client connection")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--tcp-host", default="127.0.0.1")
    parser.add_argument("--tcp-port", type=int, default=5000)
    parser.add_argument(
        "--native-scid",
        type=int,
        default=DEFAULT_NATIVE_SCID,
        help="spacecraft ID compiled into the F´ binary",
    )
    parser.add_argument(
        "--fprime-tm-port",
        type=int,
        default=52000,
        help="UDP port F´ configureSend targets (YamcsDeployment -p)",
    )
    parser.add_argument(
        "--fprime-tc-host",
        default="127.0.0.1",
        help="host where F´ configureRecv listens",
    )
    parser.add_argument(
        "--fprime-tc-port",
        type=int,
        default=52001,
        help="UDP port F´ configureRecv uses (typically -p + 1)",
    )
    parser.add_argument(
        "--spacecraft",
        action="append",
        default=[],
        metavar="TM:TC[:WIRE_SCID]",
        help=(
            "F´ TM/TC UDP ports and optional wire spacecraft ID. Repeat for "
            "multiple simulated deployments. When WIRE_SCID differs from "
            "--native-scid, TM/TC spacecraft IDs (and CRCs) are rewritten."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.spacecraft:
        links = [
            parse_spacecraft(value, args.native_scid, args.fprime_tc_host)
            for value in args.spacecraft
        ]
    else:
        links = [
            SpacecraftLink(
                tm_port=args.fprime_tm_port,
                tc_host=args.fprime_tc_host,
                tc_port=args.fprime_tc_port,
                wire_scid=args.native_scid,
                native_scid=args.native_scid,
            )
        ]
    try:
        _serve(args.tcp_host, args.tcp_port, links)
    except KeyboardInterrupt:
        print("[bridge] interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
