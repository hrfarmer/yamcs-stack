"""Multi-ground-station gateway: TM dedup, TC routing, and station registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

sys.stdout.reconfigure(line_buffering=True)

DEFAULT_STALE_AFTER = 20.0
DEFAULT_DEDUP_WINDOW = 1.5
ACTIVE_TX_PARAMETER = "/Ground/ActiveTxStation"


@dataclass
class Station:
    name: str
    tc_host: str
    tc_port: int
    last_seen: float
    tm_frames: int = 0
    tc_frames: int = 0
    source_addrs: set[str] = field(default_factory=set)

    def as_dict(self, *, active: bool, online: bool) -> dict[str, Any]:
        return {
            "name": self.name,
            "tc_host": self.tc_host,
            "tc_port": self.tc_port,
            "last_seen": self.last_seen,
            "tm_frames": self.tm_frames,
            "tc_frames": self.tc_frames,
            "active_tx": active,
            "online": online,
        }


class FrameDeduper:
    """Drop identical TM frames seen again within a short window."""

    def __init__(self, window_seconds: float) -> None:
        self.window_seconds = window_seconds
        self._seen: dict[bytes, float] = {}
        self._lock = threading.Lock()
        self.accepted = 0
        self.duplicates = 0

    def accept(self, frame: bytes) -> bool:
        digest = hashlib.sha256(frame).digest()
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            previous = self._seen.get(digest)
            if previous is not None and now - previous <= self.window_seconds:
                self.duplicates += 1
                return False
            self._seen[digest] = now
            self.accepted += 1
            return True

    def _expire(self, now: float) -> None:
        stale = [
            key
            for key, stamped in self._seen.items()
            if now - stamped > self.window_seconds
        ]
        for key in stale:
            del self._seen[key]


class Gateway:
    """Registry + UDP fan-in/fan-out between ground stations and Yamcs."""

    def __init__(
        self,
        *,
        yamcs_tm_host: str,
        yamcs_tm_port: int,
        yamcs_url: str,
        yamcs_instance: str,
        stale_after: float,
        dedup_window: float,
    ) -> None:
        self.yamcs_tm_host = yamcs_tm_host
        self.yamcs_tm_port = yamcs_tm_port
        self.yamcs_url = yamcs_url.rstrip("/")
        self.yamcs_instance = yamcs_instance
        self.stale_after = stale_after
        self.deduper = FrameDeduper(dedup_window)
        self._stations: dict[str, Station] = {}
        self._active_tx: str | None = None
        self._lock = threading.Lock()
        self._tm_out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tc_out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tc_forwarded = 0
        self.tc_dropped = 0

    def heartbeat(
        self,
        name: str,
        tc_host: str,
        tc_port: int,
        tm_frames: int = 0,
        tc_frames: int = 0,
    ) -> Station:
        if not name or "/" in name or name != name.strip():
            raise ValueError("invalid station name")
        if not 1 <= tc_port <= 65535:
            raise ValueError("tc_port out of range")
        socket.inet_aton(tc_host)
        now = time.monotonic()
        with self._lock:
            station = self._stations.get(name)
            if station is None:
                station = Station(name, tc_host, tc_port, now)
                self._stations[name] = station
                if self._active_tx is None:
                    self._active_tx = name
                    self._push_active_tx_unlocked(name)
                print(f"[gateway] registered station {name} -> {tc_host}:{tc_port}")
            else:
                station.tc_host = tc_host
                station.tc_port = tc_port
                station.last_seen = now
            station.tm_frames = tm_frames
            station.tc_frames = tc_frames
            return station

    def list_stations(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            active = self._active_tx
            return [
                station.as_dict(
                    active=station.name == active,
                    online=(now - station.last_seen) <= self.stale_after,
                )
                for station in sorted(
                    self._stations.values(), key=lambda item: item.name
                )
            ]

    def get_active_tx(self) -> str | None:
        with self._lock:
            return self._active_tx

    def set_active_tx(self, name: str, *, sync_yamcs: bool = True) -> None:
        with self._lock:
            if name not in self._stations:
                raise KeyError(name)
            self._active_tx = name
            if sync_yamcs:
                self._push_active_tx_unlocked(name)
        print(f"[gateway] active TX station -> {name}")

    def ingest_tm(self, frame: bytes, source: str) -> None:
        if not self.deduper.accept(frame):
            return
        self._tm_out.sendto(frame, (self.yamcs_tm_host, self.yamcs_tm_port))
        with self._lock:
            for station in self._stations.values():
                if source in station.source_addrs or source == station.tc_host:
                    station.source_addrs.add(source)

    def note_tm_source(self, source: str) -> None:
        with self._lock:
            for station in self._stations.values():
                if source == station.tc_host:
                    station.source_addrs.add(source)

    def forward_tc(self, frame: bytes) -> None:
        with self._lock:
            name = self._active_tx
            station = self._stations.get(name) if name else None
            if (
                station is None
                or (time.monotonic() - station.last_seen) > self.stale_after
            ):
                self.tc_dropped += 1
                print("[gateway] dropping TC: no online active TX station")
                return
            destination = (station.tc_host, station.tc_port)
        self._tc_out.sendto(frame, destination)
        self.tc_forwarded += 1
        host, port = destination
        print(f"[gateway] TC {len(frame)} bytes -> {name} {host}:{port}")

    def status(self) -> dict[str, Any]:
        return {
            "active_tx": self.get_active_tx(),
            "stations": self.list_stations(),
            "tm_accepted": self.deduper.accepted,
            "tm_duplicates": self.deduper.duplicates,
            "tc_forwarded": self.tc_forwarded,
            "tc_dropped": self.tc_dropped,
        }

    def poll_yamcs_active_tx(
        self, stop: threading.Event, interval: float = 2.0
    ) -> None:
        while not stop.wait(interval):
            try:
                value = self._read_yamcs_active_tx()
            except (OSError, urllib.error.URLError, ValueError, KeyError):
                continue
            if not value:
                continue
            with self._lock:
                if value == self._active_tx or value not in self._stations:
                    continue
                self._active_tx = value
            print(f"[gateway] active TX station from Yamcs -> {value}")

    def _push_active_tx_unlocked(self, name: str) -> None:
        threading.Thread(
            target=self._set_yamcs_active_tx,
            args=(name,),
            daemon=True,
        ).start()

    def _parameter_url(self) -> str:
        # Yamcs accepts the qualified name as a multi-segment path.
        return (
            f"{self.yamcs_url}/api/processors/{self.yamcs_instance}/realtime/"
            f"parameters{ACTIVE_TX_PARAMETER}"
        )

    def _set_yamcs_active_tx(self, name: str) -> None:
        payload = json.dumps({"type": "STRING", "stringValue": name}).encode("utf-8")
        request = urllib.request.Request(
            self._parameter_url(),
            data=payload,
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                response.read()
        except (OSError, urllib.error.URLError) as exc:
            print(f"[gateway] failed to publish ActiveTxStation to Yamcs: {exc}")

    def _read_yamcs_active_tx(self) -> str | None:
        with urllib.request.urlopen(self._parameter_url(), timeout=5) as response:  # noqa: S310
            body = json.load(response)
        eng = body.get("engValue") or body.get("rawValue") or {}
        if isinstance(eng, dict):
            return eng.get("stringValue") or eng.get("value")
        return None


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>PROVES Ground Stations</title>
  <style>
    :root {
      color-scheme: light;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
    }
    body { margin: 2rem; background: #f3f0e8; color: #1c1a16; }
    h1 { font-family: "IBM Plex Serif", Georgia, serif; font-weight: 600; }
    table {
      border-collapse: collapse;
      width: min(720px, 100%);
      background: #fffdf8;
    }
    th, td {
      text-align: left;
      padding: 0.6rem 0.8rem;
      border-bottom: 1px solid #d9d2c4;
    }
    button, select { font: inherit; padding: 0.35rem 0.7rem; }
    .ok { color: #0b6e4f; } .stale { color: #8a4b08; }
    .meta { margin: 1rem 0; color: #5c564c; }
  </style>
</head>
<body>
  <h1>Ground Stations</h1>
  <p class="meta">Select the TX station used for Yamcs telecommands.
  Telemetry from all online stations is deduplicated into Yamcs.</p>
  <p>Active TX:
    <select id="active"></select>
    <button id="apply" type="button">Apply</button>
  </p>
  <table>
    <thead>
      <tr><th>Name</th><th>TC endpoint</th><th>Status</th><th>TX</th></tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <p class="meta" id="stats"></p>
  <script>
    async function refresh() {
      const status = await (await fetch('/api/status')).json();
      const select = document.getElementById('active');
      const current = status.active_tx || '';
      select.innerHTML = status.stations.map(s => {
        const selected = s.name === current ? 'selected' : '';
        return `<option value="${s.name}" ${selected}>${s.name}</option>`;
      }).join('');
      document.getElementById('rows').innerHTML = status.stations.map(s => `
        <tr>
          <td>${s.name}</td>
          <td>${s.tc_host}:${s.tc_port}</td>
          <td class="${s.online ? 'ok' : 'stale'}">
            ${s.online ? 'online' : 'stale'}
          </td>
          <td>${s.active_tx ? 'yes' : ''}</td>
        </tr>`).join('');
      document.getElementById('stats').textContent =
        `TM accepted ${status.tm_accepted}, duplicates ${status.tm_duplicates}, ` +
        `TC forwarded ${status.tc_forwarded}, dropped ${status.tc_dropped}`;
    }
    document.getElementById('apply').onclick = async () => {
      const name = document.getElementById('active').value;
      await fetch('/api/active-tx', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name}),
      });
      await refresh();
    };
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def make_handler(gateway: Gateway):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            print(f"[gateway-http] {self.address_string()} {format % args}")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, code: int, payload: Any) -> None:
            self._send(
                code,
                json.dumps(payload).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/status":
                self._send_json(200, gateway.status())
                return
            if path == "/api/stations":
                self._send_json(200, {"stations": gateway.list_stations()})
                return
            if path == "/api/active-tx":
                self._send_json(200, {"name": gateway.get_active_tx()})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            parts = path.strip("/").split("/")
            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "stations"
                and parts[3] == "heartbeat"
            ):
                name = unquote(parts[2])
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                    station = gateway.heartbeat(
                        name,
                        str(body["tc_host"]),
                        int(body["tc_port"]),
                        int(body.get("tm_frames", 0)),
                        int(body.get("tc_frames", 0)),
                    )
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    OSError,
                    json.JSONDecodeError,
                ) as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(
                    200,
                    station.as_dict(
                        active=station.name == gateway.get_active_tx(),
                        online=True,
                    ),
                )
                return
            self._send_json(404, {"error": "not found"})

        def do_PUT(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/active-tx":
                self._send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
                name = str(body["name"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            try:
                gateway.set_active_tx(name, sync_yamcs=True)
            except KeyError:
                self._send_json(404, {"error": "unknown station"})
                return
            self._send_json(200, {"name": gateway.get_active_tx()})

    return Handler


def _udp_loop(
    bind_host: str,
    bind_port: int,
    on_datagram,
    label: str,
) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind_host, bind_port))
    print(f"[gateway] {label} listening on udp://{bind_host}:{bind_port}")

    def run() -> None:
        while True:
            data, addr = sock.recvfrom(65535)
            on_datagram(data, addr)

    threading.Thread(target=run, daemon=True).start()
    return sock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--api-host", default="0.0.0.0")
    parser.add_argument("--api-port", type=int, default=8091)
    parser.add_argument("--tm-bind-host", default="0.0.0.0")
    parser.add_argument("--tm-bind-port", type=int, default=51000)
    parser.add_argument("--tc-bind-host", default="0.0.0.0")
    parser.add_argument("--tc-bind-port", type=int, default=50001)
    parser.add_argument("--yamcs-tm-host", default="127.0.0.1")
    parser.add_argument("--yamcs-tm-port", type=int, default=50000)
    parser.add_argument("--yamcs-url", default="http://127.0.0.1:8090")
    parser.add_argument("--yamcs-instance", default="fprime-project")
    parser.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER)
    parser.add_argument("--dedup-window", type=float, default=DEFAULT_DEDUP_WINDOW)
    parser.add_argument(
        "--web-dir",
        type=Path,
        default=None,
        help="optional extra static files directory (unused; UI is embedded)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gateway = Gateway(
        yamcs_tm_host=args.yamcs_tm_host,
        yamcs_tm_port=args.yamcs_tm_port,
        yamcs_url=args.yamcs_url,
        yamcs_instance=args.yamcs_instance,
        stale_after=args.stale_after,
        dedup_window=args.dedup_window,
    )
    stop = threading.Event()
    _udp_loop(
        args.tm_bind_host,
        args.tm_bind_port,
        lambda data, addr: gateway.ingest_tm(data, addr[0]),
        "TM ingest",
    )
    _udp_loop(
        args.tc_bind_host,
        args.tc_bind_port,
        lambda data, _addr: gateway.forward_tc(data),
        "TC from Yamcs",
    )
    threading.Thread(
        target=gateway.poll_yamcs_active_tx,
        args=(stop,),
        daemon=True,
    ).start()
    server = ThreadingHTTPServer((args.api_host, args.api_port), make_handler(gateway))
    print(f"[gateway] API/UI on http://{args.api_host}:{args.api_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[gateway] interrupted")
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
