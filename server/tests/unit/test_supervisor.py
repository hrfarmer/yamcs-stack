from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from proves_yamcs.supervisor import wait_until_grafana_ready, wait_until_ready


class _Handler(BaseHTTPRequestHandler):
    yamcs_state = "RUNNING"
    grafana_database = "ok"
    plugin_enabled = True

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/instances/fprime-project":
            payload: dict | list = {"state": self.yamcs_state}
        elif self.path == "/api/health":
            payload = {"database": self.grafana_database, "version": "13.0.7"}
        elif self.path == "/api/plugins/jaops-yamcs-app/settings":
            payload = {"id": "jaops-yamcs-app", "enabled": self.plugin_enabled}
        else:
            self.send_error(404)
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


@pytest.fixture
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_wait_until_ready_accepts_running_instance(http_server):
    wait_until_ready(http_server, "fprime-project", timeout=3)


def test_wait_until_grafana_ready_accepts_healthy_database(http_server):
    wait_until_grafana_ready(http_server, timeout=3)


def test_wait_until_grafana_ready_times_out_when_unhealthy(http_server):
    _Handler.grafana_database = "fail"
    try:
        with pytest.raises(TimeoutError, match="Grafana did not become ready"):
            wait_until_grafana_ready(http_server, timeout=1)
    finally:
        _Handler.grafana_database = "ok"


def test_wait_until_grafana_ready_times_out_when_plugin_disabled(http_server):
    _Handler.plugin_enabled = False
    try:
        with pytest.raises(TimeoutError, match="Grafana did not become ready"):
            wait_until_grafana_ready(http_server, timeout=1)
    finally:
        _Handler.plugin_enabled = True
