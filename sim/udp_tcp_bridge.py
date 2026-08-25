#!/usr/bin/env python3
"""Bridge F´ Drv.Udp CCSDS frames to the GS client's TCP bent-pipe.

F´ with ``-a HOST -p PORT`` sends TM to HOST:PORT and receives TC on HOST:PORT+1.
The GS client connects as a TCP client and expects a bidirectional byte stream of
fixed-length CCSDS frames. This process joins those two transports.
"""

from __future__ import annotations

import argparse
import selectors
import socket
import sys

sys.stdout.reconfigure(line_buffering=True)


def _serve(
    tcp_host: str,
    tcp_port: int,
    fprime_tm_port: int,
    fprime_tc_host: str,
    fprime_tc_port: int,
    tc_frame_length: int | None,
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
                        if tc_frame_length is None:
                            tc_sock.sendto(data, (fprime_tc_host, fprime_tc_port))
                            continue
                        tc_buffer.extend(data)
                        while len(tc_buffer) >= tc_frame_length:
                            frame = bytes(tc_buffer[:tc_frame_length])
                            del tc_buffer[:tc_frame_length]
                            tc_sock.sendto(frame, (fprime_tc_host, fprime_tc_port))
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
    parser.add_argument(
        "--tc-frame-length",
        type=int,
        default=None,
        help="if set, split GS-client TCP TC stream into fixed-length UDP datagrams",
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
            args.tc_frame_length,
        )
    except KeyboardInterrupt:
        print("[bridge] interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
