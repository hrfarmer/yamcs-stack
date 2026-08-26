"""Radio-board setting schemas used by the gateway API and UI."""

from __future__ import annotations

from typing import Any

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

CIRCUITPYTHON_MODES = {
    "1": "SF8 / 125 kHz up and down",
    "2": "SF8 / 500 kHz up, 125 kHz down",
    "3": "SF7 / 125 kHz up and down",
    "4": "SF7 / 500 kHz up, 125 kHz down",
    "U": "Uplink only (SF7 / 500 kHz)",
}

GRC_DEFAULT_FREQUENCY_HZ = 437_400_000
GRC_SPREADING_FACTORS = (7, 8, 9, 10)
GRC_BANDWIDTH_KHZ = (125, 250, 500)
GRC_CODING_RATES = (5, 6, 7, 8)


class RadioError(ValueError):
    """Raised when a station radio payload is invalid."""


def normalize_radio_type(value: str | None) -> str:
    key = "" if value is None else str(value).strip().lower()
    if key not in RADIO_ALIASES:
        raise RadioError(
            f"radio_type must be 'circuitpython', 'grc', or 'none'; got {value!r}"
        )
    return RADIO_ALIASES[key]


def radio_schema(radio_type: str) -> dict[str, Any]:
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
    try:
        frequency = int(settings.get("frequency_hz", GRC_DEFAULT_FREQUENCY_HZ))
        spreading = int(settings.get("spreading_factor", 8))
        bandwidth_tx = int(settings.get("bandwidth_tx_khz", 125))
        bandwidth_rx = int(settings.get("bandwidth_rx_khz", 125))
        coding_rate = int(settings.get("coding_rate", 5))
    except (TypeError, ValueError) as exc:
        raise RadioError("grc settings must be integers") from exc
    if not 137_000_000 <= frequency <= 1_020_000_000:
        raise RadioError("frequency_hz must be between 137 MHz and 1020 MHz")
    if spreading not in GRC_SPREADING_FACTORS:
        raise RadioError(f"spreading_factor must be one of {GRC_SPREADING_FACTORS}")
    if bandwidth_tx not in GRC_BANDWIDTH_KHZ or bandwidth_rx not in GRC_BANDWIDTH_KHZ:
        raise RadioError(f"bandwidth must be one of {GRC_BANDWIDTH_KHZ} kHz")
    if coding_rate not in GRC_CODING_RATES:
        raise RadioError(f"coding_rate must be one of {GRC_CODING_RATES}")
    return {
        "frequency_hz": frequency,
        "spreading_factor": spreading,
        "bandwidth_tx_khz": bandwidth_tx,
        "bandwidth_rx_khz": bandwidth_rx,
        "coding_rate": coding_rate,
    }
