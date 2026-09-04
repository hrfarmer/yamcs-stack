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

from proves_yamcs.deployments import (
    RuntimeDeployment,
    RuntimeManifest,
    load_runtime_manifest,
)
from proves_yamcs.radio import (
    RADIO_NONE,
    RadioError,
    normalize_radio_type,
    radio_schema,
    validate_radio_settings,
)

sys.stdout.reconfigure(line_buffering=True)

DEFAULT_STALE_AFTER = 20.0
DEFAULT_DEDUP_WINDOW = 1.5
ACTIVE_TX_PARAMETER = "/Ground/ActiveTxStation"


def extract_tm_spacecraft_id(frame: bytes) -> int | None:
    """Return the 10-bit CCSDS TM spacecraft ID, or None if the frame is short."""
    if len(frame) < 2:
        return None
    return ((frame[0] << 8) | frame[1]) >> 4 & 0x3FF


@dataclass
class Station:
    name: str
    tc_host: str
    tc_port: int
    last_seen: float
    tm_frames: int = 0
    tc_frames: int = 0
    source_addrs: set[str] = field(default_factory=set)
    radio_type: str = RADIO_NONE
    radio_applied: dict[str, Any] = field(default_factory=dict)
    radio_desired: dict[str, Any] | None = None
    radio_error: str | None = None

    def radio_dict(self) -> dict[str, Any] | None:
        if self.radio_type == RADIO_NONE:
            return None
        payload: dict[str, Any] = {
            "type": self.radio_type,
            "applied": dict(self.radio_applied),
            "desired": dict(self.radio_desired)
            if self.radio_desired is not None
            else dict(self.radio_applied),
            "schema": radio_schema(self.radio_type),
            "pending": self.radio_desired is not None
            and self.radio_desired != self.radio_applied,
        }
        if self.radio_error:
            payload["error"] = self.radio_error
        return payload

    def as_dict(self, *, active: bool, online: bool) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "tc_host": self.tc_host,
            "tc_port": self.tc_port,
            "last_seen": self.last_seen,
            "tm_frames": self.tm_frames,
            "tc_frames": self.tc_frames,
            "active_tx": active,
            "online": online,
        }
        radio = self.radio_dict()
        if radio is not None:
            payload["radio"] = radio
        return payload


@dataclass
class DeploymentStats:
    deployment: RuntimeDeployment
    tm_accepted: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.deployment.name,
            "instance": self.deployment.instance,
            "spacecraft_id": self.deployment.spacecraft_id,
            "tm_port": self.deployment.tm_port,
            "tm_accepted": self.tm_accepted,
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
        manifest: RuntimeManifest,
        yamcs_url: str,
        stale_after: float,
        dedup_window: float,
    ) -> None:
        self.yamcs_tm_host = yamcs_tm_host
        self.manifest = manifest
        self.yamcs_url = yamcs_url.rstrip("/")
        self.stale_after = stale_after
        self.deduper = FrameDeduper(dedup_window)
        self._stations: dict[str, Station] = {}
        self._active_tx: str | None = None
        self._lock = threading.Lock()
        self._tm_out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tc_out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._by_scid = {
            item.spacecraft_id: DeploymentStats(item) for item in manifest.deployments
        }
        self.tm_unknown = 0
        self.tc_forwarded = 0
        self.tc_dropped = 0

    @property
    def instances(self) -> tuple[str, ...]:
        return tuple(item.instance for item in self.manifest.deployments)

    def heartbeat(
        self,
        name: str,
        tc_host: str,
        tc_port: int,
        tm_frames: int = 0,
        tc_frames: int = 0,
        radio: dict[str, Any] | None = None,
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
            self._update_radio_unlocked(station, radio)
            return station

    def _update_radio_unlocked(
        self, station: Station, radio: dict[str, Any] | None
    ) -> None:
        if not radio:
            return
        if not isinstance(radio, dict):
            raise ValueError("radio must be an object")
        radio_type = normalize_radio_type(radio.get("type"))
        if radio_type == RADIO_NONE:
            return
        applied_raw = radio.get("applied") or {}
        if not isinstance(applied_raw, dict):
            raise ValueError("radio.applied must be an object")
        applied = (
            validate_radio_settings(radio_type, applied_raw) if applied_raw else {}
        )
        if station.radio_type == RADIO_NONE:
            station.radio_type = radio_type
            print(f"[gateway] station {station.name} radio type -> {radio_type}")
        elif station.radio_type != radio_type:
            raise ValueError(
                f"station {station.name} radio type is {station.radio_type}, "
                f"cannot change to {radio_type}"
            )
        station.radio_applied = applied
        station.radio_error = str(radio.get("error")) if radio.get("error") else None
        if station.radio_desired == applied:
            station.radio_desired = None

    def get_station(self, name: str) -> Station:
        with self._lock:
            station = self._stations.get(name)
            if station is None:
                raise KeyError(name)
            return station

    def set_radio_settings(self, name: str, settings: dict[str, Any]) -> Station:
        with self._lock:
            station = self._stations.get(name)
            if station is None:
                raise KeyError(name)
            if station.radio_type == RADIO_NONE:
                raise RadioError("station has not advertised a radio control port")
            station.radio_desired = validate_radio_settings(
                station.radio_type, settings
            )
            station.radio_error = None
            print(
                f"[gateway] radio desired for {name} ({station.radio_type}): "
                f"{station.radio_desired}"
            )
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
        spacecraft_id = extract_tm_spacecraft_id(frame)
        stats = self._by_scid.get(spacecraft_id) if spacecraft_id is not None else None
        if stats is None:
            with self._lock:
                self.tm_unknown += 1
            print(f"[gateway] dropping TM: unknown SCID {spacecraft_id}")
            return
        if not self.deduper.accept(frame):
            return
        self._tm_out.sendto(frame, (self.yamcs_tm_host, stats.deployment.tm_port))
        with self._lock:
            stats.tm_accepted += 1
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
        with self._lock:
            deployments = [item.as_dict() for item in self._by_scid.values()]
            tm_unknown = self.tm_unknown
        deployments.sort(key=lambda item: item["name"])
        return {
            "active_tx": self.get_active_tx(),
            "stations": self.list_stations(),
            "deployments": deployments,
            "tm_accepted": self.deduper.accepted,
            "tm_duplicates": self.deduper.duplicates,
            "tm_unknown": tm_unknown,
            "tc_forwarded": self.tc_forwarded,
            "tc_dropped": self.tc_dropped,
        }

    def poll_yamcs_active_tx(
        self, stop: threading.Event, interval: float = 2.0
    ) -> None:
        while not stop.wait(interval):
            for instance in self.instances:
                try:
                    value = self._read_yamcs_active_tx(instance)
                except (OSError, urllib.error.URLError, ValueError, KeyError):
                    continue
                if not value:
                    continue
                with self._lock:
                    if value == self._active_tx or value not in self._stations:
                        continue
                    self._active_tx = value
                    self._push_active_tx_unlocked(value)
                print(f"[gateway] active TX station from Yamcs -> {value}")
                break

    def _push_active_tx_unlocked(self, name: str) -> None:
        threading.Thread(
            target=self._set_yamcs_active_tx,
            args=(name,),
            daemon=True,
        ).start()

    def _parameter_url(self, instance: str) -> str:
        # Yamcs accepts the qualified name as a multi-segment path.
        return (
            f"{self.yamcs_url}/api/processors/{instance}/realtime/"
            f"parameters{ACTIVE_TX_PARAMETER}"
        )

    def _set_yamcs_active_tx(self, name: str) -> None:
        payload = json.dumps({"type": "STRING", "stringValue": name}).encode("utf-8")
        for instance in self.instances:
            request = urllib.request.Request(
                self._parameter_url(instance),
                data=payload,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                    response.read()
            except (OSError, urllib.error.URLError) as exc:
                print(
                    f"[gateway] failed to publish ActiveTxStation to {instance}: {exc}"
                )

    def _read_yamcs_active_tx(self, instance: str) -> str | None:
        with urllib.request.urlopen(  # noqa: S310
            self._parameter_url(instance), timeout=5
        ) as response:
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
      margin-bottom: 1.5rem;
    }
    th, td {
      text-align: left;
      padding: 0.6rem 0.8rem;
      border-bottom: 1px solid #d9d2c4;
    }
    button, select, input { font: inherit; padding: 0.35rem 0.7rem; }
    label { display: block; margin: 0.4rem 0 0.15rem; }
    .ok { color: #0b6e4f; } .stale { color: #8a4b08; }
    .pending { color: #8a4b08; }
    .meta { margin: 1rem 0; color: #5c564c; }
    .panel {
      width: min(720px, 100%);
      background: #fffdf8;
      border: 1px solid #d9d2c4;
      padding: 1rem 1.1rem;
      margin: 0 0 1.5rem;
    }
    .row { display: flex; gap: 0.6rem; align-items: flex-end; flex-wrap: wrap; }
    .error { color: #9b1c1c; }
  </style>
</head>
<body>
  <h1>Ground Stations</h1>
  <p class="meta">Select the TX station used for Yamcs telecommands.
  Telemetry from all online stations is routed by spacecraft ID into the
  matching Yamcs instance. Stations with a radio control port can change
  CircuitPython passthrough mode or Ground Radio Controller frequency from
  this page.</p>
  <p>Active TX:
    <select id="active"></select>
    <button id="apply" type="button">Apply</button>
  </p>
  <table>
    <thead>
      <tr>
        <th>Name</th><th>TC endpoint</th><th>Radio</th>
        <th>Status</th><th>TX</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="panel" id="radio-panel" hidden>
    <h2 id="radio-title">Radio settings</h2>
    <p class="meta" id="radio-meta"></p>
    <form id="radio-form"></form>
    <p class="row">
      <button id="radio-apply" type="button">Apply radio settings</button>
      <span class="meta" id="radio-status"></span>
    </p>
    <p class="error" id="radio-error"></p>
  </div>
  <h1>Deployments</h1>
  <table>
    <thead>
      <tr>
        <th>Name</th><th>Instance</th><th>SCID</th>
        <th>TM port</th><th>Accepted</th>
      </tr>
    </thead>
    <tbody id="deployments"></tbody>
  </table>
  <p class="meta" id="stats"></p>
  <script>
    const RADIO_LABELS = {
      circuitpython: 'CircuitPython passthrough',
      grc: 'Ground Radio Controller',
    };
    let lastStations = [];
    let selectedRadio = '';

    function radioLabel(type) {
      return RADIO_LABELS[type] || type || 'none';
    }

    function fieldValue(field, values) {
      const raw = values[field.name];
      return raw === undefined || raw === null ? field.default : raw;
    }

    function renderRadioForm(station) {
      const form = document.getElementById('radio-form');
      const radio = station.radio;
      const values = radio.desired || radio.applied || {};
      form.innerHTML = (radio.schema && radio.schema.fields || []).map(field => {
        const current = fieldValue(field, values);
        if (field.kind === 'enum') {
          const options = field.options.map(opt => {
            const selected = String(opt.value) === String(current) ? 'selected' : '';
            return `<option value="${opt.value}" ${selected}>${opt.label}</option>`;
          }).join('');
          return `<label>${field.label}
            <select name="${field.name}">${options}</select></label>`;
        }
        return `<label>${field.label}
          <input name="${field.name}" type="number"
            min="${field.minimum || ''}" max="${field.maximum || ''}"
            value="${current}"></label>`;
      }).join('');
      document.getElementById('radio-title').textContent =
        `Radio settings — ${station.name}`;
      document.getElementById('radio-meta').textContent =
        radioLabel(radio.type) +
        (radio.pending ? ' (waiting for the station to apply)' : '');
      document.getElementById('radio-error').textContent = radio.error || '';
      document.getElementById('radio-panel').hidden = false;
    }

    function readFormSettings(form) {
      const settings = {};
      for (const element of form.elements) {
        if (!element.name) continue;
        if (element.type === 'number') settings[element.name] = Number(element.value);
        else settings[element.name] = element.value;
      }
      return settings;
    }

    async function refresh() {
      const status = await (await fetch('/api/status')).json();
      lastStations = status.stations || [];
      const select = document.getElementById('active');
      const current = status.active_tx || '';
      select.innerHTML = lastStations.map(s => {
        const selected = s.name === current ? 'selected' : '';
        return `<option value="${s.name}" ${selected}>${s.name}</option>`;
      }).join('');
      if (!selectedRadio) {
        const withRadio = lastStations.find(s => s.radio && s.radio.type);
        selectedRadio = withRadio ? withRadio.name : '';
      }
      document.getElementById('rows').innerHTML = lastStations.map(s => {
        const radio = s.radio ? radioLabel(s.radio.type) : '—';
        const pending = s.radio && s.radio.pending ? ' pending' : '';
        const chosen = s.name === selectedRadio ? ' style="font-weight:600"' : '';
        return `<tr data-station="${s.name}"${chosen}>
          <td>${s.name}</td>
          <td>${s.tc_host}:${s.tc_port}</td>
          <td class="${pending ? 'pending' : ''}">${radio}${pending}</td>
          <td class="${s.online ? 'ok' : 'stale'}">
            ${s.online ? 'online' : 'stale'}
          </td>
          <td>${s.active_tx ? 'yes' : ''}</td>
        </tr>`;
      }).join('');
      const station = lastStations.find(s => s.name === selectedRadio && s.radio);
      if (station) {
        const form = document.getElementById('radio-form');
        const editing = form.contains(document.activeElement);
        if (!form.innerHTML || !editing) renderRadioForm(station);
        else {
          document.getElementById('radio-meta').textContent =
            radioLabel(station.radio.type) +
            (station.radio.pending ? ' (waiting for the station to apply)' : '');
          document.getElementById('radio-error').textContent =
            station.radio.error || '';
        }
      } else document.getElementById('radio-panel').hidden = true;
      document.getElementById('deployments').innerHTML =
        (status.deployments || []).map(d => `
        <tr>
          <td>${d.name}</td>
          <td>${d.instance}</td>
          <td>${d.spacecraft_id}</td>
          <td>${d.tm_port}</td>
          <td>${d.tm_accepted}</td>
        </tr>`).join('');
      document.getElementById('stats').textContent =
        `TM accepted ${status.tm_accepted}, duplicates ${status.tm_duplicates}, ` +
        `unknown SCID ${status.tm_unknown || 0}, ` +
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
    document.getElementById('rows').onclick = (event) => {
      const row = event.target.closest('tr[data-station]');
      if (!row) return;
      selectedRadio = row.getAttribute('data-station');
      const station = lastStations.find(s => s.name === selectedRadio);
      if (station && station.radio) renderRadioForm(station);
    };
    document.getElementById('radio-apply').onclick = async () => {
      if (!selectedRadio) return;
      const settings = readFormSettings(document.getElementById('radio-form'));
      const status = document.getElementById('radio-status');
      const error = document.getElementById('radio-error');
      status.textContent = 'saving…';
      error.textContent = '';
      const response = await fetch(
        '/api/stations/' + encodeURIComponent(selectedRadio) + '/radio',
        {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({settings}),
        }
      );
      const body = await response.json();
      if (!response.ok) {
        status.textContent = '';
        error.textContent = body.error || 'request failed';
        return;
      }
      status.textContent = 'queued for the station';
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
            parts = path.strip("/").split("/")
            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "stations"
                and parts[3] == "radio"
            ):
                name = unquote(parts[2])
                try:
                    station = gateway.get_station(name)
                except KeyError:
                    self._send_json(404, {"error": "unknown station"})
                    return
                radio = station.radio_dict()
                if radio is None:
                    self._send_json(404, {"error": "station has no radio"})
                    return
                self._send_json(200, radio)
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
                        body.get("radio"),
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
            parts = path.strip("/").split("/")
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "stations"
                and parts[3] == "radio"
            ):
                name = unquote(parts[2])
                settings = body.get("settings", body)
                try:
                    station = gateway.set_radio_settings(name, settings)
                except KeyError:
                    self._send_json(404, {"error": "unknown station"})
                    return
                except (RadioError, ValueError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, station.radio_dict())
                return
            if path != "/api/active-tx":
                self._send_json(404, {"error": "not found"})
                return
            try:
                name = str(body["name"])
            except (KeyError, TypeError) as exc:
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
    parser.add_argument("--yamcs-url", default="http://127.0.0.1:8090")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("runtime/config/deployments.json"),
    )
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
    manifest = load_runtime_manifest(args.manifest)
    gateway = Gateway(
        yamcs_tm_host=args.yamcs_tm_host,
        manifest=manifest,
        yamcs_url=args.yamcs_url,
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
    for item in manifest.deployments:
        print(
            f"[gateway] {item.name} SCID {item.spacecraft_id} -> "
            f"{args.yamcs_tm_host}:{item.tm_port} ({item.instance})"
        )
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
