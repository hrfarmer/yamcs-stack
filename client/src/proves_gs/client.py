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
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fprime_gds.common.communication.ccsds.space_data_link import (
    SpaceDataLinkFramerDeframer,
)

from proves_gs.authentication import AuthenticateFramer
from proves_gs.bundle import load_bundle
from proves_gs.config import (
    ClientConfig,
    ConfigError,
    SatelliteConfig,
    apply_overrides,
    load_config,
)
from proves_gs.radio import (
    RADIO_CIRCUITPYTHON,
    RADIO_NONE,
    RadioController,
    RadioError,
    load_grc_opcodes,
    normalize_radio_type,
)

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


def extract_tm_spacecraft_id(frame: bytes) -> int:
    return ((frame[0] << 8) | frame[1]) >> 4 & 0x3FF


def extract_tc_spacecraft_id(frame: bytes) -> int:
    """Return the 10-bit CCSDS TC transfer-frame spacecraft ID."""
    if len(frame) < 2:
        raise ValueError("TC frame too short to read spacecraft ID")
    return ((frame[0] << 8) | frame[1]) & 0x3FF


class TMFrameScanner:
    """Incrementally extract CRC-valid TM frames with per-SCID lengths."""

    def __init__(self, frame_lengths: dict[int, int], vc_id: int = 1) -> None:
        if not frame_lengths:
            raise ValueError("at least one spacecraft ID is required")
        self.frame_lengths = frame_lengths
        self.vc_id = vc_id
        self.syncs: list[tuple[bytes, int, int]] = []
        for spacecraft_id, length in frame_lengths.items():
            word = (spacecraft_id << 4) | (vc_id << 1)
            sync = bytes([(word >> 8) & 0xFF, word & 0xFF])
            self.syncs.append((sync, spacecraft_id, length))
        self.buffer = bytearray()
        self.junk_bytes = 0
        self.frame_gaps = 0
        self.last_vc_count: dict[int, int] = {}

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        frames: list[bytes] = []
        while True:
            matches = [
                (position, spacecraft_id, length)
                for sync, spacecraft_id, length in self.syncs
                if (position := self.buffer.find(sync)) != -1
            ]
            if not matches:
                if len(self.buffer) > 1:
                    self.junk_bytes += len(self.buffer) - 1
                    del self.buffer[: len(self.buffer) - 1]
                break
            start, spacecraft_id, frame_length = min(matches, key=lambda item: item[0])
            if start:
                self.junk_bytes += start
                del self.buffer[:start]
            if len(self.buffer) < frame_length:
                break

            candidate = bytes(self.buffer[:frame_length])
            expected_crc = int.from_bytes(candidate[-2:], "big")
            if crc16_ccitt(candidate[:-2]) != expected_crc:
                del self.buffer[:2]
                self.junk_bytes += 2
                continue

            del self.buffer[:frame_length]
            vc_count = candidate[3]
            previous = self.last_vc_count.get(spacecraft_id)
            if previous is not None:
                distance = (vc_count - previous) & 0xFF
                if distance > 1:
                    self.frame_gaps += distance - 1
            self.last_vc_count[spacecraft_id] = vc_count
            frames.append(candidate)
        return frames


@dataclass
class SatelliteRuntime:
    """Loaded bundle plus framers for one satellite."""

    name: str
    spacecraft_id: int
    frame_length: int
    auth_framer: AuthenticateFramer | None
    data_link_framer: SpaceDataLinkFramerDeframer


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


def wrap_tc_passthrough(
    tc_transfer_frame: bytes,
    data_link_framer: SpaceDataLinkFramerDeframer,
) -> bytes:
    """Space-data-link wrap without PROVES HMAC (stock ComCcsds F´)."""
    space_packet = extract_space_packet(tc_transfer_frame)
    fix_ccsds_primary_header(space_packet)
    return data_link_framer.frame(bytes(space_packet))


def select_satellite_for_tc(
    tc_transfer_frame: bytes, satellites: dict[int, SatelliteRuntime]
) -> SatelliteRuntime | None:
    spacecraft_id = extract_tc_spacecraft_id(tc_transfer_frame)
    return satellites.get(spacecraft_id)


def _load_satellite_runtime(config: SatelliteConfig, vc_id: int) -> SatelliteRuntime:
    bundle = load_bundle(config.input_dir, require_auth_key=not config.skip_auth)
    auth_framer: AuthenticateFramer | None
    if config.skip_auth:
        auth_framer = None
        print(f"[client] {config.name}: PROVES HMAC disabled (skip_auth)")
    elif config.auth_key is not None:
        auth_framer = AuthenticateFramer(
            config.auth_key, config.sequence_number_file, config.spi
        )
    else:
        key_file = config.auth_key_file or bundle.auth_key_path
        if key_file is None:
            raise SystemExit(
                f"authentication key file is required for {config.name} "
                "unless skip_auth"
            )
        sequence_file = config.sequence_number_file
        if sequence_file is None:
            raise SystemExit(f"sequence_number_file is required for {config.name}")
        auth_framer = AuthenticateFramer.from_key_file(
            key_file, sequence_file, config.spi
        )
    if config.sequence_number_file is None and auth_framer is not None:
        raise SystemExit(f"sequence_number_file is required for {config.name}")
    data_link_framer = SpaceDataLinkFramerDeframer(
        scid=bundle.spacecraft_id, vcid=vc_id, frame_size=bundle.frame_length
    )
    print(
        f"[client] {config.name}: SCID {bundle.spacecraft_id}, "
        f"frame length {bundle.frame_length}"
    )
    return SatelliteRuntime(
        name=config.name,
        spacecraft_id=bundle.spacecraft_id,
        frame_length=bundle.frame_length,
        auth_framer=auth_framer,
        data_link_framer=data_link_framer,
    )


def _forward_tm_serial(
    serial_port: BinaryIO,
    tm_socket: socket.socket,
    server_host: str,
    tm_port: int,
    frame_lengths: dict[int, int],
    vc_id: int,
    stats: dict[str, int],
) -> None:
    scanner = TMFrameScanner(frame_lengths, vc_id)
    counts: Counter[int] = Counter()
    stats_started = time.monotonic()
    print(f"[TM] serial -> UDP {server_host}:{tm_port} (SCIDs {sorted(frame_lengths)})")
    while True:
        chunk = serial_port.read(max(1, getattr(serial_port, "in_waiting", 0) or 1))
        for frame in scanner.feed(chunk):
            tm_socket.sendto(frame, (server_host, tm_port))
            counts[extract_tm_spacecraft_id(frame)] += 1
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


def _forward_tc(
    tc_socket: socket.socket,
    writer,
    satellites: dict[int, SatelliteRuntime],
    transport: str,
    stats: dict[str, int],
) -> None:
    print(f"[TC] UDP -> SCID-aware wrap -> {transport}")
    while True:
        transfer_frame, _ = tc_socket.recvfrom(4096)
        try:
            satellite = select_satellite_for_tc(transfer_frame, satellites)
        except ValueError as exc:
            print(f"[TC] dropping malformed frame: {exc}")
            continue
        if satellite is None:
            spacecraft_id = extract_tc_spacecraft_id(transfer_frame)
            print(f"[TC] dropping frame for unknown SCID {spacecraft_id}")
            continue
        try:
            if satellite.auth_framer is None:
                output = wrap_tc_passthrough(transfer_frame, satellite.data_link_framer)
            else:
                output = wrap_tc(
                    transfer_frame,
                    satellite.auth_framer,
                    satellite.data_link_framer,
                )
        except ValueError as exc:
            print(f"[TC] dropping malformed frame: {exc}")
            continue
        writer(output)
        stats["tc_frames"] += 1
        print(
            f"[TC] #{stats['tc_frames']} {satellite.name}: "
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
    radio: RadioController | None = None,
) -> None:
    endpoint = f"{api_url.rstrip('/')}/api/stations/{station_name}/heartbeat"
    print(f"[register] heartbeating to {endpoint} every {interval:.0f}s")
    while True:
        payload_body: dict = {
            "tc_host": tc_host,
            "tc_port": tc_port,
            "tm_frames": stats["tm_frames"],
            "tc_frames": stats["tc_frames"],
        }
        if radio is not None and radio.radio_type != RADIO_NONE:
            payload_body["radio"] = radio.status()
        payload = json.dumps(payload_body).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
            _apply_desired_radio(radio, body)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            print(f"[register] heartbeat failed: {exc}")
        if stop.wait(interval):
            return


def _apply_desired_radio(radio: RadioController | None, body: dict) -> None:
    if radio is None or radio.radio_type == RADIO_NONE:
        return
    radio_body = body.get("radio") if isinstance(body, dict) else None
    if not isinstance(radio_body, dict):
        return
    desired = radio_body.get("desired")
    if not isinstance(desired, dict) or not desired:
        return
    if desired == (radio.applied or {}):
        return
    try:
        applied = radio.apply(desired)
    except RadioError as exc:
        print(f"[radio] failed to apply settings: {exc}")
        return
    print(f"[radio] applied {radio.radio_type} settings: {applied}")


def _drain_control_port(control_port, stop: threading.Event) -> None:
    """Keep the control UART from filling up with console / F´ downlink."""
    while not stop.is_set():
        try:
            waiting = getattr(control_port, "in_waiting", 0) or 0
            chunk = control_port.read(max(1, waiting))
        except OSError as exc:
            print(f"[radio] control port read failed: {exc}")
            return
        if chunk:
            text = chunk.decode("utf-8", errors="replace").strip()
            if text:
                for line in text.splitlines():
                    print(f"[radio-console] {line}")
        else:
            stop.wait(0.1)


def _detect_tc_host(explicit: str | None, server_host: str) -> str:
    if explicit:
        return explicit
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((server_host, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="TOML config file (see config/*.example.toml)",
    )
    parser.add_argument("--mode", choices=["serial", "tcp"])
    parser.add_argument("--uart-device", help="data UART (alias: --uart-data-device)")
    parser.add_argument("--uart-data-device", dest="uart_device")
    parser.add_argument("--uart-control-device")
    parser.add_argument("--uart-baud", type=int)
    parser.add_argument(
        "--radio-type",
        choices=[
            "none",
            "circuitpython",
            "grc",
            "circuit-python-passthrough",
            "ground-radio-controller",
        ],
    )
    parser.add_argument("--radio-scid", type=int)
    parser.add_argument("--tcp-host")
    parser.add_argument("--tcp-port", type=int)
    parser.add_argument("--server-host")
    parser.add_argument("--server-tm-port", type=int)
    parser.add_argument("--tc-listen-host")
    parser.add_argument("--tc-listen-port", type=int)
    parser.add_argument("--tc-advertise-host")
    parser.add_argument("--gateway-api-url")
    parser.add_argument("--station-name")
    parser.add_argument("--heartbeat-interval", type=float)
    parser.add_argument("--vc-id", type=int)
    return parser


def parse_config(argv: list[str] | None = None) -> ClientConfig:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc

    try:
        return apply_overrides(
            config,
            {
                "mode": args.mode,
                "uart_device": args.uart_device,
                "uart_control_device": args.uart_control_device,
                "uart_baud": args.uart_baud,
                "radio_type": args.radio_type,
                "radio_scid": args.radio_scid,
                "tcp_host": args.tcp_host,
                "tcp_port": args.tcp_port,
                "server_host": args.server_host,
                "server_tm_port": args.server_tm_port,
                "tc_listen_host": args.tc_listen_host,
                "tc_listen_port": args.tc_listen_port,
                "tc_advertise_host": args.tc_advertise_host,
                "gateway_api_url": args.gateway_api_url,
                "station_name": args.station_name,
                "heartbeat_interval": args.heartbeat_interval,
                "vc_id": args.vc_id,
            },
        )
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    config = parse_config(argv)
    runtimes = [
        _load_satellite_runtime(satellite, config.vc_id)
        for satellite in config.satellites
    ]
    spacecraft_ids = [item.spacecraft_id for item in runtimes]
    if len(spacecraft_ids) != len(set(spacecraft_ids)):
        raise SystemExit("satellite bundles must have unique spacecraft IDs")
    satellites = {item.spacecraft_id: item for item in runtimes}
    frame_lengths = {item.spacecraft_id: item.frame_length for item in runtimes}
    stats = {"tm_frames": 0, "tc_frames": 0}
    tm_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tc_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tc_socket.bind((config.tc_listen_host, config.tc_listen_port))
    advertise_host = _detect_tc_host(config.tc_advertise_host, config.server_host)
    api_url = config.gateway_api_url or f"http://{config.server_host}:8091"
    stop = threading.Event()
    radio_type = normalize_radio_type(config.radio_type)
    radio: RadioController | None = None
    control_port = None

    if config.mode == "serial":
        import serial

        print(f"[serial] data {config.uart_device} at {config.uart_baud} baud")
        transport = serial.Serial(config.uart_device, config.uart_baud, timeout=0.1)
        with contextlib.suppress(AttributeError, OSError):
            transport.set_buffer_size(rx_size=65536)
        if radio_type != RADIO_NONE:
            print(
                f"[serial] control {config.uart_control_device} "
                f"({radio_type}) at {config.uart_baud} baud"
            )
            control_port = serial.Serial(
                config.uart_control_device, config.uart_baud, timeout=0.1
            )
            radio = RadioController(
                radio_type=radio_type,
                control_port=control_port,
                scid=config.radio_scid,
                vcid=config.vc_id,
                opcodes=load_grc_opcodes(config.radio_dictionary)
                if radio_type != RADIO_CIRCUITPYTHON
                else None,
            )
        tm_target = _forward_tm_serial
        tm_args = (
            transport,
            tm_socket,
            config.server_host,
            config.server_tm_port,
            frame_lengths,
            config.vc_id,
            stats,
        )
        writer = transport.write
    else:
        lengths = {item.frame_length for item in runtimes}
        if len(lengths) != 1:
            raise SystemExit(
                "tcp mode requires every [[satellite]] to share a frame length"
            )
        print(f"[tcp] connecting to {config.tcp_host}:{config.tcp_port}")
        transport = socket.create_connection((config.tcp_host, config.tcp_port))
        tm_target = _forward_tm_tcp
        tm_args = (
            transport,
            tm_socket,
            config.server_host,
            config.server_tm_port,
            lengths.pop(),
            stats,
        )
        writer = transport.sendall

    print(
        f"[station] name={config.station_name} advertise TC to "
        f"{advertise_host}:{config.tc_listen_port}"
    )
    tm_thread = threading.Thread(target=tm_target, args=tm_args, daemon=True)
    tc_thread = threading.Thread(
        target=_forward_tc,
        args=(tc_socket, writer, satellites, config.mode, stats),
        daemon=True,
    )
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(
            api_url,
            config.station_name,
            advertise_host,
            config.tc_listen_port,
            stats,
            config.heartbeat_interval,
            stop,
            radio,
        ),
        daemon=True,
    )
    tm_thread.start()
    tc_thread.start()
    heartbeat_thread.start()
    if control_port is not None:
        threading.Thread(
            target=_drain_control_port,
            args=(control_port, stop),
            daemon=True,
        ).start()
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
        if control_port is not None:
            control_port.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
