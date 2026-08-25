#!/usr/bin/env python3
"""Bridge F´ Drv.Udp CCSDS frames to the GS client's TCP bent-pipe.

F´ with ``-a HOST -p PORT`` sends TM to HOST:PORT and receives TC on HOST:PORT+1.
The GS client connects as a TCP client and expects a bidirectional byte stream.
TM frames from F´ are fixed-length and forwarded as-is. TC frames from the GS
client are variable-length CCSDS TC transfer frames; this process reassembles
them from the TCP stream using the length field in the TC primary header.
"""

from __future__ import annotations

import argparse
import selectors
import socket
import sys

sys.stdout.reconfigure(line_buffering=True)

TC_HEADER_SIZE = 5


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


def _serve(
    tcp_host: str,
    tcp_port: int,
    fprime_tm_port: int,
    fprime_tc_host: str,
    fprime_tc_port: int,
) -> None:
    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_server.bind((tcp_host, tcp_port))
    tcp_server.listen(1)
    print(f"[bridge] waiting for GS client on tcp://{tcp_host}:{tcp_port}")

    tm_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tm_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tm_sock.bind(("0.0.0.0", fprime_tm_port))
    tc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(
        f"[bridge] F´ TM udp://0.0.0.0:{fprime_tm_port} -> TCP; "
        f"TCP TC -> udp://{fprime_tc_host}:{fprime_tc_port}"
    )

    while True:
        client, addr = tcp_server.accept()
        print(f"[bridge] GS client connected from {addr[0]}:{addr[1]}")
        client.setblocking(False)
        tm_sock.setblocking(False)
        selector = selectors.DefaultSelector()
        selector.register(client, selectors.EVENT_READ)
        selector.register(tm_sock, selectors.EVENT_READ)
        tc_buffer = bytearray()
        try:
            while True:
                for key, _ in selector.select(timeout=1.0):
                    if key.fileobj is tm_sock:
                        frame, _ = tm_sock.recvfrom(65535)
                        client.sendall(frame)
                    else:
                        data = client.recv(65535)
                        if not data:
                            raise ConnectionError("GS client disconnected")
                        tc_buffer.extend(data)
                        for frame in pop_tc_frames(tc_buffer):
                            tc_sock.sendto(frame, (fprime_tc_host, fprime_tc_port))
                            print(
                                f"[bridge] TC {len(frame)} bytes -> "
                                f"{fprime_tc_host}:{fprime_tc_port}"
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _serve(
            args.tcp_host,
            args.tcp_port,
            args.fprime_tm_port,
            args.fprime_tc_host,
            args.fprime_tc_port,
        )
    except KeyboardInterrupt:
        print("[bridge] interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
