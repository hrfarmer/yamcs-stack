"""Authentication framing used on the PROVES telecommand path."""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
from pathlib import Path

from proves_gs.bundle import AUTH_KEY_PATTERN, BundleError, load_auth_key


class SequenceStore:
    """Persistent, process-local sequence counter with atomic updates."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()

    def _read(self) -> int:
        try:
            value = int(self.path.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"invalid authentication sequence state {self.path}: {exc}"
            ) from exc
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"authentication sequence number is out of range: {value}")
        return value

    def next(self) -> int:
        """Return the current number and atomically persist its successor."""
        with self._lock:
            current = self._read()
            successor = (current + 1) & 0xFFFFFFFF
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            temporary.write_text(str(successor), encoding="ascii")
            os.replace(temporary, self.path)
            return current


class AuthenticateFramer:
    """Add the PROVES SPI, sequence number, and truncated HMAC-SHA256."""

    def __init__(
        self,
        authentication_key: str,
        sequence_file: str | Path,
        spi: int = 0,
    ) -> None:
        key = authentication_key.removeprefix("0x").removeprefix("0X")
        if AUTH_KEY_PATTERN.fullmatch(key) is None:
            raise BundleError(
                "authentication key must be exactly 32 hexadecimal characters"
            )
        if not 0 <= spi <= 0xFFFF:
            raise ValueError("SPI must fit in 16 bits")
        self.key = bytes.fromhex(key)
        self.spi = spi
        self.sequence_store = SequenceStore(sequence_file)

    @classmethod
    def from_key_file(
        cls,
        key_file: str | Path,
        sequence_file: str | Path,
        spi: int = 0,
    ) -> AuthenticateFramer:
        return cls(load_auth_key(Path(key_file)), sequence_file, spi)

    def frame(self, data: bytes) -> bytes:
        sequence_number = self.sequence_store.next()
        authenticated = (
            self.spi.to_bytes(2, byteorder="big")
            + sequence_number.to_bytes(4, byteorder="big")
            + data
        )
        digest = hmac.new(self.key, authenticated, hashlib.sha256).digest()
        return authenticated + digest[:16]
