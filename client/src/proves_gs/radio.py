"""Radio-board control port protocols (CircuitPython passthrough and GRC)."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from fprime_gds.common.communication.ccsds.space_data_link import (
    SpaceDataLinkFramerDeframer,
)

RADIO_NONE = "none"
RADIO_CIRCUITPYTHON = "circuitpython"
RADIO_GRC = "grc"

RADIO_ALIASES = {
    "": RADIO_NONE,
    "none": RADIO_NONE,
    "circuitpython": RADIO_CIRCUITPYTHON,
    "circuit-python": RADIO_CIRCUITPYTHON,
    "circuit-python-passthrough": RADIO_CIRCUITPYTHON,
    "passthrough": RADIO_CIRCUITPYTHON,
    "grc": RADIO_GRC,
    "ground-radio-controller": RADIO_GRC,
}

# Console tokens accepted by proves-core-reference circuit-python-passthrough.
CIRCUITPYTHON_MODES = {
    "1": "SF8 / 125 kHz up and down",
    "2": "SF8 / 500 kHz up, 125 kHz down",
    "3": "SF7 / 125 kHz up and down",
    "4": "SF7 / 500 kHz up, 125 kHz down",
    "U": "Uplink only (SF7 / 500 kHz)",
}

# GRC `uhf` instance base id from ground-radio-controller instances.fpp.
GRC_UHF_BASE_ID = 0x10017000
# Parameter SET/SAVE pairs occupy opcodes 0-7; CONTINUOUS_WAVE is 8; SET_FREQ is 9.
GRC_DEFAULT_OPCODES = {
    "CODING_RATE_PRM_SET": GRC_UHF_BASE_ID + 0,
    "DATA_RATE_PRM_SET": GRC_UHF_BASE_ID + 2,
    "BANDWIDTH_TX_PRM_SET": GRC_UHF_BASE_ID + 4,
    "BANDWIDTH_RX_PRM_SET": GRC_UHF_BASE_ID + 6,
    "SET_FREQ": GRC_UHF_BASE_ID + 9,
}
GRC_COMMAND_NAMES = {
    "SET_FREQ": "ReferenceDeployment.uhf.SET_FREQ",
    "CODING_RATE_PRM_SET": "ReferenceDeployment.uhf.CODING_RATE_PRM_SET",
    "DATA_RATE_PRM_SET": "ReferenceDeployment.uhf.DATA_RATE_PRM_SET",
    "BANDWIDTH_TX_PRM_SET": "ReferenceDeployment.uhf.BANDWIDTH_TX_PRM_SET",
    "BANDWIDTH_RX_PRM_SET": "ReferenceDeployment.uhf.BANDWIDTH_RX_PRM_SET",
}
GRC_DEFAULT_FREQUENCY_HZ = 437_400_000
GRC_SPREADING_FACTORS = (7, 8, 9, 10)
GRC_BANDWIDTH_KHZ = (125, 250, 500)
GRC_CODING_RATES = (5, 6, 7, 8)
# GRC LoRaBandwidth enum values (0/1/2), not kHz.
GRC_BANDWIDTH_ENUM = {125: 0, 250: 1, 500: 2}
GRC_CODING_RATE_ENUM = {5: 1, 6: 2, 7: 3, 8: 4}

FW_PACKET_COMMAND = 0
SPACE_PACKET_HEADER_SIZE = 6
SPACE_PACKET_TYPE_TC = 1
SPACE_PACKET_UNSEGMENTED = 0b11
_space_packet_sequence = itertools.count()


class RadioError(ValueError):
    """Raised when radio settings or control-port encoding are invalid."""


def normalize_radio_type(value: str | None) -> str:
    key = "" if value is None else str(value).strip().lower()
    if key not in RADIO_ALIASES:
        raise RadioError(
            f"radio_type must be 'circuitpython', 'grc', or 'none'; got {value!r}"
        )
    return RADIO_ALIASES[key]


def radio_schema(radio_type: str) -> dict[str, Any]:
    """Return the gateway-UI field schema for a radio type."""
    kind = normalize_radio_type(radio_type)
    if kind == RADIO_CIRCUITPYTHON:
        return {
            "type": RADIO_CIRCUITPYTHON,
            "fields": [
                {
                    "name": "mode",
                    "label": "LoRa mode",
                    "kind": "enum",
                    "options": [
                        {"value": key, "label": f"{key}: {label}"}
                        for key, label in CIRCUITPYTHON_MODES.items()
                    ],
                    "default": "1",
                }
            ],
        }
    if kind == RADIO_GRC:
        return {
            "type": RADIO_GRC,
            "fields": [
                {
                    "name": "frequency_hz",
                    "label": "Center frequency (Hz)",
                    "kind": "integer",
                    "minimum": 137_000_000,
                    "maximum": 1_020_000_000,
                    "default": GRC_DEFAULT_FREQUENCY_HZ,
                },
                {
                    "name": "spreading_factor",
                    "label": "Spreading factor",
                    "kind": "enum",
                    "options": [
                        {"value": value, "label": f"SF{value}"}
                        for value in GRC_SPREADING_FACTORS
                    ],
                    "default": 8,
                },
                {
                    "name": "bandwidth_tx_khz",
                    "label": "TX bandwidth (kHz)",
                    "kind": "enum",
                    "options": [
                        {"value": value, "label": f"{value} kHz"}
                        for value in GRC_BANDWIDTH_KHZ
                    ],
                    "default": 125,
                },
                {
                    "name": "bandwidth_rx_khz",
                    "label": "RX bandwidth (kHz)",
                    "kind": "enum",
                    "options": [
                        {"value": value, "label": f"{value} kHz"}
                        for value in GRC_BANDWIDTH_KHZ
                    ],
                    "default": 125,
                },
                {
                    "name": "coding_rate",
                    "label": "Coding rate (4/n)",
                    "kind": "enum",
                    "options": [
                        {"value": value, "label": f"4/{value}"}
                        for value in GRC_CODING_RATES
                    ],
                    "default": 5,
                },
            ],
        }
    return {"type": RADIO_NONE, "fields": []}


def validate_radio_settings(
    radio_type: str, settings: dict[str, Any]
) -> dict[str, Any]:
    """Return a normalized settings dict, or raise RadioError."""
    kind = normalize_radio_type(radio_type)
    if not isinstance(settings, dict):
        raise RadioError("radio settings must be an object")
    if kind == RADIO_NONE:
        if settings:
            raise RadioError("radio settings are not supported without a radio type")
        return {}
    if kind == RADIO_CIRCUITPYTHON:
        mode = str(settings.get("mode", "1")).strip().upper()
        if mode not in CIRCUITPYTHON_MODES:
            raise RadioError(
                f"circuitpython mode must be one of {sorted(CIRCUITPYTHON_MODES)}"
            )
        unknown = sorted(set(settings) - {"mode"})
        if unknown:
            raise RadioError(f"unknown circuitpython settings: {', '.join(unknown)}")
        return {"mode": mode}
    known = {
        "frequency_hz",
        "spreading_factor",
        "bandwidth_tx_khz",
        "bandwidth_rx_khz",
        "coding_rate",
    }
    unknown = sorted(set(settings) - known)
    if unknown:
        raise RadioError(f"unknown grc settings: {', '.join(unknown)}")
    frequency = int(settings.get("frequency_hz", GRC_DEFAULT_FREQUENCY_HZ))
    if not 137_000_000 <= frequency <= 1_020_000_000:
        raise RadioError("frequency_hz must be between 137 MHz and 1020 MHz")
    spreading = int(settings.get("spreading_factor", 8))
    if spreading not in GRC_SPREADING_FACTORS:
        raise RadioError(f"spreading_factor must be one of {GRC_SPREADING_FACTORS}")
    bandwidth_tx = int(settings.get("bandwidth_tx_khz", 125))
    bandwidth_rx = int(settings.get("bandwidth_rx_khz", 125))
    if bandwidth_tx not in GRC_BANDWIDTH_KHZ or bandwidth_rx not in GRC_BANDWIDTH_KHZ:
        raise RadioError(f"bandwidth must be one of {GRC_BANDWIDTH_KHZ} kHz")
    coding_rate = int(settings.get("coding_rate", 5))
    if coding_rate not in GRC_CODING_RATES:
        raise RadioError(f"coding_rate must be one of {GRC_CODING_RATES}")
    return {
        "frequency_hz": frequency,
        "spreading_factor": spreading,
        "bandwidth_tx_khz": bandwidth_tx,
        "bandwidth_rx_khz": bandwidth_rx,
        "coding_rate": coding_rate,
    }


def encode_ccsds_space_packet(
    user_data: bytes,
    *,
    apid: int,
    sequence_count: int,
) -> bytes:
    """Build a CCSDS space packet (TC, unsegmented) around user_data."""
    if not user_data:
        raise RadioError("space packet user data must not be empty")
    identification = (
        (0 << 13) | (SPACE_PACKET_TYPE_TC << 12) | (0 << 11) | (apid & 0x7FF)
    )
    sequence = ((SPACE_PACKET_UNSEGMENTED & 0x3) << 14) | (sequence_count & 0x3FFF)
    length = len(user_data) - 1
    return (
        identification.to_bytes(2, "big")
        + sequence.to_bytes(2, "big")
        + length.to_bytes(2, "big")
        + user_data
    )


def encode_fprime_ccsds_command(
    opcode: int,
    args: bytes,
    *,
    scid: int,
    vcid: int,
    sequence_count: int | None = None,
) -> bytes:
    """Frame an F´ command as a CCSDS space packet inside a TC transfer frame.

    GRC ComCcsds uplink is TC frame -> SpacePacketDeframer -> FprimeRouter ->
    CmdDispatcher, which deserializes Fw::CmdPacket as descriptor + opcode + args.
    """
    descriptor = FW_PACKET_COMMAND.to_bytes(2, "big")
    command = descriptor + opcode.to_bytes(4, "big") + args
    seq = sequence_count if sequence_count is not None else next(_space_packet_sequence)
    space_packet = encode_ccsds_space_packet(
        command, apid=FW_PACKET_COMMAND, sequence_count=seq
    )
    return SpaceDataLinkFramerDeframer(scid=scid, vcid=vcid, frame_size=None).frame(
        space_packet
    )


def load_grc_opcodes(dictionary_path: Path | None) -> dict[str, int]:
    """Return SET_FREQ / param opcodes, optionally overridden from a GRC dictionary."""
    opcodes = dict(GRC_DEFAULT_OPCODES)
    if dictionary_path is None:
        return opcodes
    try:
        raw = json.loads(dictionary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RadioError(
            f"unable to load radio dictionary {dictionary_path}: {exc}"
        ) from exc
    by_name = {
        entry.get("name"): entry.get("opcode")
        for entry in raw.get("commands", [])
        if isinstance(entry, dict)
    }
    for key, qualified in GRC_COMMAND_NAMES.items():
        opcode = by_name.get(qualified)
        if opcode is None:
            continue
        try:
            opcodes[key] = int(opcode)
        except (TypeError, ValueError) as exc:
            raise RadioError(
                f"invalid opcode for {qualified} in {dictionary_path}"
            ) from exc
    return opcodes


def encode_circuitpython_settings(settings: dict[str, Any]) -> bytes:
    normalized = validate_radio_settings(RADIO_CIRCUITPYTHON, settings)
    return f"{normalized['mode']}\n".encode("ascii")


def encode_grc_settings(
    settings: dict[str, Any],
    *,
    scid: int,
    vcid: int,
    opcodes: dict[str, int] | None = None,
) -> list[bytes]:
    """Return one CCSDS TC frame per GRC setting that should be applied.

    Parameter SET commands only store values. SET_FREQ is sent last because
    its handler calls enableRx() and programs the radio with the stored params.
    """
    normalized = validate_radio_settings(RADIO_GRC, settings)
    table = opcodes or GRC_DEFAULT_OPCODES
    return [
        encode_fprime_ccsds_command(
            table["DATA_RATE_PRM_SET"],
            bytes([normalized["spreading_factor"]]),
            scid=scid,
            vcid=vcid,
        ),
        encode_fprime_ccsds_command(
            table["BANDWIDTH_TX_PRM_SET"],
            bytes([GRC_BANDWIDTH_ENUM[normalized["bandwidth_tx_khz"]]]),
            scid=scid,
            vcid=vcid,
        ),
        encode_fprime_ccsds_command(
            table["BANDWIDTH_RX_PRM_SET"],
            bytes([GRC_BANDWIDTH_ENUM[normalized["bandwidth_rx_khz"]]]),
            scid=scid,
            vcid=vcid,
        ),
        encode_fprime_ccsds_command(
            table["CODING_RATE_PRM_SET"],
            bytes([GRC_CODING_RATE_ENUM[normalized["coding_rate"]]]),
            scid=scid,
            vcid=vcid,
        ),
        encode_fprime_ccsds_command(
            table["SET_FREQ"],
            normalized["frequency_hz"].to_bytes(4, "big"),
            scid=scid,
            vcid=vcid,
        ),
    ]


@dataclass
class RadioController:
    """Applies gateway radio settings to a board control serial port."""

    radio_type: str
    control_port: BinaryIO | None
    scid: int = 0x44
    vcid: int = 1
    opcodes: dict[str, int] | None = None
    applied: dict[str, Any] | None = None
    last_error: str | None = None

    def status(self) -> dict[str, Any] | None:
        if self.radio_type == RADIO_NONE:
            return None
        payload: dict[str, Any] = {
            "type": self.radio_type,
            "applied": self.applied or {},
            "schema": radio_schema(self.radio_type),
        }
        if self.last_error:
            payload["error"] = self.last_error
        return payload

    def apply(self, settings: dict[str, Any]) -> dict[str, Any]:
        if self.radio_type == RADIO_NONE:
            raise RadioError("this station has no radio control port")
        if self.control_port is None:
            raise RadioError("radio control port is not open")
        normalized = validate_radio_settings(self.radio_type, settings)
        try:
            if self.radio_type == RADIO_CIRCUITPYTHON:
                self.control_port.write(encode_circuitpython_settings(normalized))
            else:
                for frame in encode_grc_settings(
                    normalized,
                    scid=self.scid,
                    vcid=self.vcid,
                    opcodes=self.opcodes,
                ):
                    self.control_port.write(frame)
            flush = getattr(self.control_port, "flush", None)
            if callable(flush):
                flush()
        except OSError as exc:
            self.last_error = str(exc)
            raise RadioError(f"failed to write radio settings: {exc}") from exc
        self.applied = normalized
        self.last_error = None
        return normalized
