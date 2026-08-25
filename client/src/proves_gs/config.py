"""Load ground-station client settings from a TOML config file."""

from __future__ import annotations

import socket
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when a client config file is missing or invalid."""


@dataclass(frozen=True)
class ClientConfig:
    """Runtime settings for proves-gs-client."""

    mode: str = "serial"
    input_dir: Path = Path("inputs/proves")
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
    auth_key: str | None = None
    auth_key_file: Path | None = None
    sequence_number_file: Path = Path("runtime/state/sequence-number")
    frame_length: int | None = None
    spacecraft_ids: tuple[int, ...] | None = None
    vc_id: int = 1
    spi: int = 0
    skip_auth: bool = False

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
        if self.spacecraft_ids is not None and (
            not self.spacecraft_ids
            or any(not 0 <= item <= 0x3FF for item in self.spacecraft_ids)
        ):
            raise ConfigError("spacecraft_ids must be in the range 0..1023")
        if self.mode == "serial" and not self.uart_device.strip():
            raise ConfigError("uart_device is required for serial mode")
        if self.mode == "tcp" and not self.tcp_host.strip():
            raise ConfigError("tcp_host is required for tcp mode")


_PATH_FIELDS = {
    "input_dir",
    "auth_key_file",
    "sequence_number_file",
}
_OPTIONAL_PATH_FIELDS = {"auth_key_file"}


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
    known = {item.name for item in fields(ClientConfig)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")

    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _PATH_FIELDS:
            values[key] = _resolve_path(
                value, base_dir, required=key not in _OPTIONAL_PATH_FIELDS
            )
        elif key == "spacecraft_ids":
            if not isinstance(value, list) or not value:
                raise ConfigError(
                    "spacecraft_ids must be a non-empty array of integers"
                )
            values[key] = tuple(_as_int(item, "spacecraft_ids") for item in value)
        elif key in {
            "uart_baud",
            "tcp_port",
            "server_tm_port",
            "tc_listen_port",
            "vc_id",
            "spi",
        }:
            values[key] = _as_int(value, key)
        elif key == "frame_length":
            values[key] = None if value is None else _as_int(value, key)
        elif key == "heartbeat_interval":
            values[key] = _as_float(value, key)
        elif key == "skip_auth":
            values[key] = _as_bool(value, key)
        elif key in {
            "tc_advertise_host",
            "gateway_api_url",
            "auth_key",
        }:
            if value is None:
                values[key] = None
            else:
                values[key] = _as_str(value, key)
        else:
            values[key] = _as_str(value, key)

    if "station_name" not in values or not str(values.get("station_name", "")).strip():
        values["station_name"] = socket.gethostname()
    if "sequence_number_file" not in values:
        values["sequence_number_file"] = _resolve_path(
            "runtime/state/sequence-number", base_dir, required=True
        )
    if "input_dir" not in values:
        values["input_dir"] = _resolve_path("inputs/proves", base_dir, required=True)
    elif values["input_dir"] is None:
        raise ConfigError("input_dir is required")

    config = ClientConfig(**values)
    config.validate()
    return config


def apply_overrides(config: ClientConfig, overrides: dict[str, Any]) -> ClientConfig:
    """Return a copy of config with non-None overrides applied."""
    filtered = {key: value for key, value in overrides.items() if value is not None}
    if not filtered:
        return config
    updated = replace(config, **filtered)
    updated.validate()
    return updated
