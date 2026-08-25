"""Ground-station client: radio board passthrough to the central Yamcs gateway."""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import BinaryIO

from fprime_gds.common.communication.ccsds.space_data_link import (
    SpaceDataLinkFramerDeframer,
)

from proves_gs.authentication import AuthenticateFramer
from proves_gs.bundle import load_bundle

sys.stdout.reconfigure(line_buffering=True)

TC_FRAME_HEADER_SIZE = 5
TC_FRAME_CRC_SIZE = 2
SPACE_PACKET_HEADER_SIZE = 6
_ccsds_sequence = itertools.count()


def crc16_ccitt(data: bytes) -> int:
    """CCSDS CRC16-CCITT (poly 0x1021, initial value 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


class TMFrameScanner:
    """Incrementally extract fixed-length, CRC-valid TM frames from noisy bytes."""

    def __init__(
        self,
        frame_length: int,
        spacecraft_ids: list[int],
        vc_id: int = 1,
    ) -> None:
        self.frame_length = frame_length
        self.spacecraft_ids = spacecraft_ids
        self.vc_id = vc_id
        self.syncs = []
        for spacecraft_id in spacecraft_ids:
            word = (spacecraft_id << 4) | (vc_id << 1)
            self.syncs.append(bytes([(word >> 8) & 0xFF, word & 0xFF]))
        self.buffer = bytearray()
        self.junk_bytes = 0
        self.frame_gaps = 0
        self.last_vc_count: dict[int, int] = {}

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        frames: list[bytes] = []
        while True:
            positions = [
                position
                for sync in self.syncs
                if (position := self.buffer.find(sync)) != -1
            ]
            if not positions:
                if len(self.buffer) > 1:
                    self.junk_bytes += len(self.buffer) - 1
                    del self.buffer[: len(self.buffer) - 1]
                break
            start = min(positions)
            if start:
                self.junk_bytes += start
                del self.buffer[:start]
            if len(self.buffer) < self.frame_length:
                break

            candidate = bytes(self.buffer[: self.frame_length])
            expected_crc = int.from_bytes(candidate[-2:], "big")
            if crc16_ccitt(candidate[:-2]) != expected_crc:
                del self.buffer[:2]
                self.junk_bytes += 2
                continue

            del self.buffer[: self.frame_length]
            spacecraft_id = ((candidate[0] << 8) | candidate[1]) >> 4 & 0x3FF
            vc_count = candidate[3]
            previous = self.last_vc_count.get(spacecraft_id)
            if previous is not None:
                distance = (vc_count - previous) & 0xFF
                if distance > 1:
                    self.frame_gaps += distance - 1
            self.last_vc_count[spacecraft_id] = vc_count
            frames.append(candidate)
        return frames


def fix_ccsds_primary_header(space_packet: bytearray) -> None:
    if len(space_packet) < SPACE_PACKET_HEADER_SIZE:
        raise ValueError("CCSDS space packet is shorter than its primary header")
    packet_data_length = len(space_packet) - SPACE_PACKET_HEADER_SIZE - 1
    space_packet[4:6] = packet_data_length.to_bytes(2, "big")
    sequence = next(_ccsds_sequence) & 0x3FFF
    sequence_flags = space_packet[2] & 0xC0
    space_packet[2] = sequence_flags | ((sequence >> 8) & 0x3F)
    space_packet[3] = sequence & 0xFF


def extract_space_packet(tc_transfer_frame: bytes) -> bytearray:
    minimum = TC_FRAME_HEADER_SIZE + TC_FRAME_CRC_SIZE + 1
    if len(tc_transfer_frame) < minimum:
        raise ValueError(f"TC frame too short ({len(tc_transfer_frame)} bytes)")
    return bytearray(tc_transfer_frame[TC_FRAME_HEADER_SIZE:-TC_FRAME_CRC_SIZE])


def wrap_tc(
    tc_transfer_frame: bytes,
    auth_framer: AuthenticateFramer,
    data_link_framer: SpaceDataLinkFramerDeframer,
) -> bytes:
    space_packet = extract_space_packet(tc_transfer_frame)
    fix_ccsds_primary_header(space_packet)
    return data_link_framer.frame(auth_framer.frame(bytes(space_packet)))


def _forward_tm_serial(
    serial_port: BinaryIO,
    tm_socket: socket.socket,
    server_host: str,
    tm_port: int,
    frame_length: int,
    spacecraft_ids: list[int],
    vc_id: int,
    stats: dict[str, int],
) -> None:
    scanner = TMFrameScanner(frame_length, spacecraft_ids, vc_id)
    counts: Counter[int] = Counter()
    stats_started = time.monotonic()
    print(
        f"[TM] serial -> UDP {server_host}:{tm_port} "
        f"(frame length {frame_length}, SCIDs {spacecraft_ids})"
    )
    while True:
        chunk = serial_port.read(max(1, getattr(serial_port, "in_waiting", 0) or 1))
        for frame in scanner.feed(chunk):
            tm_socket.sendto(frame, (server_host, tm_port))
            spacecraft_id = ((frame[0] << 8) | frame[1]) >> 4 & 0x3FF
            counts[spacecraft_id] += 1
            stats["tm_frames"] += 1
        now = time.monotonic()
        if now - stats_started >= 30:
            summary = ", ".join(f"SCID {key}: {value}" for key, value in counts.items())
            print(
                f"[TM] stats: {summary or 'no frames'}, "
                f"{scanner.frame_gaps} gaps, {scanner.junk_bytes} junk bytes"
            )
            counts.clear()
            scanner.junk_bytes = 0
            stats_started = now


def _forward_tm_tcp(
    tcp_socket: socket.socket,
    tm_socket: socket.socket,
    server_host: str,
    tm_port: int,
    frame_length: int,
    stats: dict[str, int],
) -> None:
    print(f"[TM] TCP -> UDP {server_host}:{tm_port} (frame length {frame_length})")
    buffered = b""
    while True:
        chunk = tcp_socket.recv(4096)
        if not chunk:
            return
        buffered += chunk
        while len(buffered) >= frame_length:
            frame, buffered = buffered[:frame_length], buffered[frame_length:]
            tm_socket.sendto(frame, (server_host, tm_port))
            stats["tm_frames"] += 1


def wrap_tc_passthrough(
    tc_transfer_frame: bytes,
    data_link_framer: SpaceDataLinkFramerDeframer,
) -> bytes:
    """Space-data-link wrap without PROVES HMAC (stock ComCcsds F´)."""
    space_packet = extract_space_packet(tc_transfer_frame)
    fix_ccsds_primary_header(space_packet)
    return data_link_framer.frame(bytes(space_packet))


def _forward_tc(
    tc_socket: socket.socket,
    writer,
    auth_framer: AuthenticateFramer | None,
    data_link_framer: SpaceDataLinkFramerDeframer,
    transport: str,
    stats: dict[str, int],
) -> None:
    mode = "authenticate" if auth_framer is not None else "passthrough"
    print(f"[TC] UDP -> {mode} -> TC frame -> {transport}")
    while True:
        transfer_frame, _ = tc_socket.recvfrom(4096)
        try:
            if auth_framer is None:
                output = wrap_tc_passthrough(transfer_frame, data_link_framer)
            else:
                output = wrap_tc(transfer_frame, auth_framer, data_link_framer)
        except ValueError as exc:
            print(f"[TC] dropping malformed frame: {exc}")
            continue
        writer(output)
        stats["tc_frames"] += 1
        print(
            f"[TC] #{stats['tc_frames']}: "
            f"{len(transfer_frame)} bytes -> {len(output)} bytes"
        )


def _heartbeat_loop(
    api_url: str,
    station_name: str,
    tc_host: str,
    tc_port: int,
    stats: dict[str, int],
    interval: float,
    stop: threading.Event,
) -> None:
    endpoint = f"{api_url.rstrip('/')}/api/stations/{station_name}/heartbeat"
    print(f"[register] heartbeating to {endpoint} every {interval:.0f}s")
    while True:
        payload = json.dumps(
            {
                "tc_host": tc_host,
                "tc_port": tc_port,
                "tm_frames": stats["tm_frames"],
                "tc_frames": stats["tc_frames"],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                response.read()
        except (OSError, urllib.error.URLError) as exc:
            print(f"[register] heartbeat failed: {exc}")
        if stop.wait(interval):
            return


def _detect_tc_host(explicit: str | None, server_host: str) -> str:
    if explicit:
        return explicit
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((server_host, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


def _spacecraft_ids(value: str) -> list[int]:
    try:
        values = [int(item.strip(), 0) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("spacecraft IDs must be integers") from exc
    if not values or any(not 0 <= item <= 0x3FF for item in values):
        raise argparse.ArgumentTypeError("spacecraft IDs must be in the range 0..1023")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--mode", choices=["serial", "tcp"], default="serial")
    parser.add_argument("--input-dir", type=Path, default=Path("inputs/proves"))
    parser.add_argument("--uart-device", default="/dev/ttyUSB0")
    parser.add_argument("--uart-baud", type=int, default=115200)
    parser.add_argument("--tcp-host", default="127.0.0.1")
    parser.add_argument("--tcp-port", type=int, default=5000)
    parser.add_argument(
        "--server-host",
        default="127.0.0.1",
        help="central Yamcs gateway host (Tailscale name or IP)",
    )
    parser.add_argument(
        "--server-tm-port",
        type=int,
        default=51000,
        help="gateway UDP port that accepts TM frames from ground stations",
    )
    parser.add_argument(
        "--tc-listen-host",
        default="0.0.0.0",
        help="local bind address for TC frames from the gateway",
    )
    parser.add_argument(
        "--tc-listen-port",
        type=int,
        default=50001,
        help="local UDP port for TC frames from the gateway",
    )
    parser.add_argument(
        "--tc-advertise-host",
        default=None,
        help="Tailscale address the gateway should use for TC (auto-detected)",
    )
    parser.add_argument(
        "--gateway-api-url",
        default=None,
        help="gateway HTTP API base URL (default http://SERVER_HOST:8091)",
    )
    parser.add_argument(
        "--station-name",
        default=socket.gethostname(),
        help="unique ground-station name registered with the gateway",
    )
    parser.add_argument("--heartbeat-interval", type=float, default=5.0)
    parser.add_argument("--auth-key", help="hex key override; prefer --auth-key-file")
    parser.add_argument("--auth-key-file", type=Path)
    parser.add_argument(
        "--sequence-number-file",
        type=Path,
        default=Path("runtime/state/sequence-number"),
    )
    parser.add_argument("--frame-length", type=int)
    parser.add_argument("--spacecraft-id", type=_spacecraft_ids, action="append")
    parser.add_argument("--vc-id", type=int, default=1)
    parser.add_argument("--spi", type=int, default=0)
    parser.add_argument(
        "--skip-auth",
        action="store_true",
        help="omit PROVES HMAC (for stock F´ ComCcsds deployments in simulation)",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    if not 0 <= args.vc_id <= 7:
        raise ValueError("virtual channel ID must be in the range 0..7")
    if args.uart_baud < 1:
        raise ValueError("UART baud must be positive")
    if args.heartbeat_interval < 1:
        raise ValueError("heartbeat interval must be at least 1 second")
    if not args.station_name.strip():
        raise ValueError("station name must not be empty")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bundle = load_bundle(args.input_dir, require_auth_key=not args.skip_auth)
    spacecraft_ids = (
        [item for group in args.spacecraft_id for item in group]
        if args.spacecraft_id
        else [bundle.spacecraft_id]
    )
    frame_length = args.frame_length or bundle.frame_length
    auth_framer: AuthenticateFramer | None
    if args.skip_auth:
        auth_framer = None
        print("[client] PROVES HMAC authentication disabled (--skip-auth)")
    elif args.auth_key is not None:
        auth_framer = AuthenticateFramer(
            args.auth_key, args.sequence_number_file, args.spi
        )
    else:
        key_file = args.auth_key_file or bundle.auth_key_path
        if key_file is None:
            raise SystemExit("authentication key file is required unless --skip-auth")
        auth_framer = AuthenticateFramer.from_key_file(
            key_file, args.sequence_number_file, args.spi
        )

    data_link_framer = SpaceDataLinkFramerDeframer(
        scid=spacecraft_ids[0], vcid=args.vc_id, frame_size=frame_length
    )
    stats = {"tm_frames": 0, "tc_frames": 0}
    tm_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tc_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tc_socket.bind((args.tc_listen_host, args.tc_listen_port))
    advertise_host = _detect_tc_host(args.tc_advertise_host, args.server_host)
    api_url = args.gateway_api_url or f"http://{args.server_host}:8091"
    stop = threading.Event()

    if args.mode == "serial":
        import serial

        print(f"[serial] opening {args.uart_device} at {args.uart_baud} baud")
        transport = serial.Serial(args.uart_device, args.uart_baud, timeout=0.1)
        with contextlib.suppress(AttributeError, OSError):
            transport.set_buffer_size(rx_size=65536)
        tm_target = _forward_tm_serial
        tm_args = (
            transport,
            tm_socket,
            args.server_host,
            args.server_tm_port,
            frame_length,
            spacecraft_ids,
            args.vc_id,
            stats,
        )
        writer = transport.write
    else:
        print(f"[tcp] connecting to {args.tcp_host}:{args.tcp_port}")
        transport = socket.create_connection((args.tcp_host, args.tcp_port))
        tm_target = _forward_tm_tcp
        tm_args = (
            transport,
            tm_socket,
            args.server_host,
            args.server_tm_port,
            frame_length,
            stats,
        )
        writer = transport.sendall

    print(
        f"[station] name={args.station_name} advertise TC to "
        f"{advertise_host}:{args.tc_listen_port}"
    )
    tm_thread = threading.Thread(target=tm_target, args=tm_args, daemon=True)
    tc_thread = threading.Thread(
        target=_forward_tc,
        args=(tc_socket, writer, auth_framer, data_link_framer, args.mode, stats),
        daemon=True,
    )
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(
            api_url,
            args.station_name,
            advertise_host,
            args.tc_listen_port,
            stats,
            args.heartbeat_interval,
            stop,
        ),
        daemon=True,
    )
    tm_thread.start()
    tc_thread.start()
    heartbeat_thread.start()
    try:
        tm_thread.join()
        tc_thread.join()
    except KeyboardInterrupt:
        print("[client] interrupted")
    finally:
        stop.set()
        tc_socket.close()
        tm_socket.close()
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
