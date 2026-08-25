"""Load ground-station client settings from a TOML config file."""

from __future__ import annotations

import re
import socket
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(ValueError):
    """Raised when a client config file is missing or invalid."""


@dataclass(frozen=True)
class SatelliteConfig:
    """Per-satellite bundle, auth, and framing settings."""

    name: str
    input_dir: Path
    auth_key: str | None = None
    auth_key_file: Path | None = None
    sequence_number_file: Path | None = None
    spi: int = 0
    skip_auth: bool = False

    def validate(self) -> None:
        if NAME_PATTERN.fullmatch(self.name) is None:
            raise ConfigError(
                f"satellite name {self.name!r} must match {NAME_PATTERN.pattern}"
            )
        if not 0 <= self.spi <= 0xFFFF:
            raise ConfigError("spi must fit in 16 bits")


@dataclass(frozen=True)
class ClientConfig:
    """Runtime settings for proves-gs-client."""

    mode: str = "serial"
    uart_device: str = "/dev/ttyUSB0"
    uart_baud: int = 115200
    tcp_host: str = "127.0.0.1"
    tcp_port: int = 5000
    server_host: str = "127.0.0.1"
    server_tm_port: int = 51000
    tc_listen_host: str = "0.0.0.0"
    tc_listen_port: int = 50001
    tc_advertise_host: str | None = None
    gateway_api_url: str | None = None
    station_name: str = ""
    heartbeat_interval: float = 5.0
    vc_id: int = 1
    satellites: tuple[SatelliteConfig, ...] = ()

    def validate(self) -> None:
        if self.mode not in {"serial", "tcp"}:
            raise ConfigError(f"mode must be 'serial' or 'tcp', got {self.mode!r}")
        if not 0 <= self.vc_id <= 7:
            raise ConfigError("vc_id must be in the range 0..7")
        if self.uart_baud < 1:
            raise ConfigError("uart_baud must be positive")
        if self.heartbeat_interval < 1:
            raise ConfigError("heartbeat_interval must be at least 1 second")
        if not self.station_name.strip():
            raise ConfigError("station_name must not be empty")
        if self.tcp_port < 1 or self.server_tm_port < 1 or self.tc_listen_port < 1:
            raise ConfigError("ports must be positive")
        if self.mode == "serial" and not self.uart_device.strip():
            raise ConfigError("uart_device is required for serial mode")
        if self.mode == "tcp" and not self.tcp_host.strip():
            raise ConfigError("tcp_host is required for tcp mode")
        if not self.satellites:
            raise ConfigError("at least one [[satellite]] table is required")
        names = [item.name for item in self.satellites]
        if len(names) != len(set(names)):
            raise ConfigError("satellite names must be unique")
        for satellite in self.satellites:
            satellite.validate()


def _resolve_path(value: Any, base_dir: Path, *, required: bool) -> Path | None:
    if value is None:
        if required:
            raise ConfigError("path value is required")
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field} must be an integer")
    return value


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{field} must be a number")
    return float(value)


def _as_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field} must be a boolean")
    return value


def _as_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be a string")
    return value


def _load_satellite(raw: Any, base_dir: Path, index: int) -> SatelliteConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"satellite {index} must be a table")
    known = {item.name for item in fields(SatelliteConfig)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"unknown keys in satellite {index}: {', '.join(unknown)}")
    if "name" not in raw:
        raise ConfigError(f"satellite {index} is missing name")
    if "input_dir" not in raw:
        raise ConfigError(f"satellite {index} is missing input_dir")
    name = _as_str(raw["name"], "name")
    sequence_default = f"runtime/state/sequence-{name}"
    return SatelliteConfig(
        name=name,
        input_dir=_resolve_path(raw["input_dir"], base_dir, required=True),
        auth_key=(
            None
            if "auth_key" not in raw or raw["auth_key"] is None
            else _as_str(raw["auth_key"], "auth_key")
        ),
        auth_key_file=_resolve_path(raw.get("auth_key_file"), base_dir, required=False)
        if "auth_key_file" in raw
        else None,
        sequence_number_file=_resolve_path(
            raw.get("sequence_number_file", sequence_default),
            base_dir,
            required=True,
        ),
        spi=_as_int(raw.get("spi", 0), "spi"),
        skip_auth=_as_bool(raw.get("skip_auth", False), "skip_auth"),
    )


def load_config(path: Path) -> ClientConfig:
    """Load and validate a TOML client config file."""
    config_path = path.expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a table")

    base_dir = config_path.parent
    satellites_raw = raw.pop("satellite", None)
    known = {item.name for item in fields(ClientConfig)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")

    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key in {
            "uart_baud",
            "tcp_port",
            "server_tm_port",
            "tc_listen_port",
            "vc_id",
        }:
            values[key] = _as_int(value, key)
        elif key == "heartbeat_interval":
            values[key] = _as_float(value, key)
        elif key in {"tc_advertise_host", "gateway_api_url"}:
            if value is None:
                values[key] = None
            else:
                values[key] = _as_str(value, key)
        else:
            values[key] = _as_str(value, key)

    if "station_name" not in values or not str(values.get("station_name", "")).strip():
        values["station_name"] = socket.gethostname()
    if not isinstance(satellites_raw, list) or not satellites_raw:
        raise ConfigError("at least one [[satellite]] table is required")
    values["satellites"] = tuple(
        _load_satellite(entry, base_dir, index)
        for index, entry in enumerate(satellites_raw)
    )

    config = ClientConfig(**values)
    config.validate()
    return config


def apply_overrides(config: ClientConfig, overrides: dict[str, Any]) -> ClientConfig:
    """Return a copy of config with non-None station-level overrides applied."""
    filtered = {key: value for key, value in overrides.items() if value is not None}
    if not filtered:
        return config
    updated = replace(config, **filtered)
    updated.validate()
    return updated
