"""Own the central Yamcs container, Grafana, event bridge, and multi-GS gateway."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from proves_yamcs.deployments import RuntimeManifest, load_runtime_manifest

PID_FILE = Path("runtime/pids/supervisor.json")
DEFAULT_MANIFEST = Path("runtime/config/deployments.json")
COMPOSE_FILE = Path("compose.yaml")
COMPOSE_UDP = Path("runtime/compose.udp.yaml")


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    if (
        subprocess.run(
            ["docker", "compose", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    ):
        command = ["docker", "compose"]
    elif executable := shutil.which("docker-compose"):
        command = [executable]
    else:
        raise RuntimeError("Docker Compose is not installed")
    files = ["-f", str(COMPOSE_FILE)]
    if COMPOSE_UDP.is_file():
        files.extend(["-f", str(COMPOSE_UDP)])
    return subprocess.run([*command, *files, *args], check=check)


def _http_json(url: str) -> dict | list:
    with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
        return json.load(response)


def wait_until_ready(url: str, instance: str, timeout: int = 180) -> None:
    wait_until_instances_ready(url, [instance], timeout)


def wait_until_instances_ready(
    url: str, instances: Sequence[str], timeout: int = 180
) -> None:
    deadline = time.monotonic() + timeout
    remaining = set(instances)
    last_error: Exception | None = None
    while remaining and time.monotonic() < deadline:
        for instance in list(remaining):
            try:
                state = _http_json(f"{url}/api/instances/{instance}")
                if isinstance(state, dict) and state.get("state") == "RUNNING":
                    print(f"Yamcs instance {instance} is RUNNING")
                    remaining.discard(instance)
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last_error = exc
        if remaining:
            time.sleep(1)
    if remaining:
        raise TimeoutError(
            f"Yamcs instances {sorted(remaining)} did not become ready "
            f"within {timeout}s: {last_error}"
        )


def wait_until_grafana_ready(url: str, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    base = url.rstrip("/")
    while time.monotonic() < deadline:
        try:
            state = _http_json(f"{base}/api/health")
            if not (isinstance(state, dict) and state.get("database") == "ok"):
                time.sleep(1)
                continue
            plugin = _http_json(f"{base}/api/plugins/jaops-yamcs-app/settings")
            if (
                isinstance(plugin, dict)
                and plugin.get("id") == "jaops-yamcs-app"
                and plugin.get("enabled")
            ):
                print(f"Grafana is ready at {url} with jaops-yamcs-app")
                return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(1)
    raise TimeoutError(
        f"Grafana did not become ready at {url} within {timeout}s: {last_error}"
    )


def _compose_stack(*extra: str) -> subprocess.CompletedProcess:
    return _compose("up", "--build", "-d", "yamcs", "grafana", *extra)


def _terminate(process: subprocess.Popen, timeout: int = 10) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=timeout)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def stop() -> None:
    try:
        record = json.loads(PID_FILE.read_text(encoding="utf-8"))
        supervisor_pid = int(record["supervisor"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        supervisor_pid = 0
    if supervisor_pid and supervisor_pid != os.getpid():
        try:
            os.kill(supervisor_pid, signal.SIGTERM)
            for _ in range(50):
                try:
                    os.kill(supervisor_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
        except ProcessLookupError:
            pass
    _compose("down", "--remove-orphans", check=False)
    PID_FILE.unlink(missing_ok=True)


def _load_manifest(path: Path) -> RuntimeManifest:
    return load_runtime_manifest(path)


def run(args: argparse.Namespace) -> int:
    stop()
    manifest = _load_manifest(args.manifest)
    _compose_stack()
    wait_until_instances_ready(
        args.yamcs_url,
        [item.instance for item in manifest.deployments],
        args.timeout,
    )
    wait_until_grafana_ready(args.grafana_url, args.timeout)

    event_command_base = [
        str(Path(sys.executable).with_name("fprime-yamcs-events")),
        "--yamcs-url",
        args.yamcs_url,
    ]
    gateway_command = [
        sys.executable,
        "-m",
        "proves_yamcs.gateway",
        "--api-host",
        args.gateway_api_host,
        "--api-port",
        str(args.gateway_api_port),
        "--tm-bind-port",
        str(args.tm_ingest_port),
        "--tc-bind-port",
        str(args.tc_from_yamcs_port),
        "--yamcs-tm-host",
        args.yamcs_tm_host,
        "--yamcs-url",
        args.yamcs_url,
        "--manifest",
        str(args.manifest),
        "--dedup-window",
        str(args.dedup_window),
    ]

    event_processes = [
        subprocess.Popen(
            [
                *event_command_base,
                "--instance",
                item.instance,
                "--dictionary",
                item.dictionary,
            ],
            start_new_session=True,
        )
        for item in manifest.deployments
    ]
    gateway_process = subprocess.Popen(gateway_command, start_new_session=True)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(
        json.dumps(
            {
                "supervisor": os.getpid(),
                "events": [process.pid for process in event_processes],
                "gateway": gateway_process.pid,
            }
        ),
        encoding="utf-8",
    )

    stopping = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        while not stopping:
            gateway_status = gateway_process.poll()
            if gateway_status is not None:
                return gateway_status
            for process in event_processes:
                event_status = process.poll()
                if event_status is not None:
                    raise RuntimeError(
                        f"event bridge exited unexpectedly with {event_status}"
                    )
            time.sleep(0.25)
    finally:
        _terminate(gateway_process)
        for process in event_processes:
            _terminate(process)
        _compose("down", "--remove-orphans", check=False)
        PID_FILE.unlink(missing_ok=True)
    return 0


def check_server(args: argparse.Namespace) -> int:
    _compose("down", "--remove-orphans", check=False)
    manifest = _load_manifest(args.manifest)
    try:
        _compose_stack()
        wait_until_instances_ready(
            args.yamcs_url,
            [item.instance for item in manifest.deployments],
            args.timeout,
        )
        wait_until_grafana_ready(args.grafana_url, args.timeout)
        server = _http_json(f"{args.yamcs_url}/api")
        if not isinstance(server, dict) or not server.get("yamcsVersion"):
            raise RuntimeError("Yamcs server metadata did not include a version")
        for item in manifest.deployments:
            commands = _http_json(
                f"{args.yamcs_url}/api/mdb/{item.instance}/commands?limit=1"
            )
            parameters = _http_json(
                f"{args.yamcs_url}/api/mdb/{item.instance}/parameters?limit=1"
            )
            links = _http_json(f"{args.yamcs_url}/api/links/{item.instance}")
            if not isinstance(commands, dict) or not commands.get("commands"):
                raise RuntimeError(
                    f"Yamcs MDB for {item.instance} did not expose any commands"
                )
            if not isinstance(parameters, dict) or not parameters.get("parameters"):
                raise RuntimeError(
                    f"Yamcs MDB for {item.instance} did not expose any parameters"
                )
            if not isinstance(links, dict):
                raise RuntimeError(
                    f"Yamcs links endpoint for {item.instance} returned an "
                    "invalid response"
                )
            link_states = {
                link.get("name"): link.get("status") for link in links.get("links", [])
            }
            unhealthy = [
                name
                for name in ("UDP_TM_IN", "UDP_TC_OUT")
                if link_states.get(name) != "OK"
            ]
            if unhealthy:
                raise RuntimeError(
                    f"Yamcs links for {item.instance} are not healthy: "
                    f"{', '.join(unhealthy)}"
                )
        archive_root = Path("runtime/data")
        archive_identities = {
            path.relative_to(archive_root): path.read_bytes()
            for path in archive_root.glob("*.rdb/IDENTITY")
        }
        for item in manifest.deployments:
            identity = Path(f"{item.instance}.rdb/IDENTITY")
            if identity not in archive_identities:
                raise RuntimeError(
                    f"Yamcs did not create persistent archive data for {item.instance}"
                )

        _compose("restart", "yamcs")
        wait_until_instances_ready(
            args.yamcs_url,
            [item.instance for item in manifest.deployments],
            args.timeout,
        )
        if any(
            not (archive_root / path).is_file()
            or (archive_root / path).read_bytes() != identity
            for path, identity in archive_identities.items()
        ):
            raise RuntimeError("Yamcs archive data did not survive container restart")
        grafana = _http_json(f"{args.grafana_url.rstrip('/')}/api/health")
        if not isinstance(grafana, dict) or grafana.get("database") != "ok":
            raise RuntimeError("Grafana health endpoint did not report database ok")
        plugin = _http_json(
            f"{args.grafana_url.rstrip('/')}/api/plugins/jaops-yamcs-app/settings"
        )
        if not isinstance(plugin, dict) or not plugin.get("enabled", True):
            raise RuntimeError("Grafana JAOPS Yamcs app is not enabled")
        datasources = _http_json(f"{args.grafana_url.rstrip('/')}/api/datasources")
        if not isinstance(datasources, list) or not any(
            isinstance(item, dict) and item.get("uid") == "jaops-yamcs-main"
            for item in datasources
        ):
            raise RuntimeError("Grafana did not provision the JAOPS Yamcs datasource")
        search = _http_json(f"{args.grafana_url.rstrip('/')}/api/search?type=dash-db")
        if not isinstance(search, list):
            raise RuntimeError("Grafana dashboard search returned an invalid response")
        by_folder: dict[str, set[str]] = {}
        for entry in search:
            if not isinstance(entry, dict):
                continue
            folder = str(entry.get("folderTitle") or "")
            title = str(entry.get("title") or "")
            by_folder.setdefault(folder, set()).add(title)
        for item in manifest.deployments:
            titles = by_folder.get(item.name, set())
            missing = [
                name
                for name in (f"{item.name} Overview", f"{item.name} Commanding")
                if name not in titles
            ]
            if missing:
                raise RuntimeError(
                    f"Grafana folder {item.name!r} is missing dashboards: "
                    f"{', '.join(missing)}"
                )
        print(f"Yamcs {server['yamcsVersion']} + Grafana build check passed")
        return 0
    finally:
        _compose("down", "--remove-orphans", check=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run_parser.add_argument("--yamcs-url", default="http://localhost:8090")
    run_parser.add_argument("--grafana-url", default="http://localhost:3000")
    run_parser.add_argument("--timeout", type=int, default=180)
    run_parser.add_argument("--gateway-api-host", default="0.0.0.0")
    run_parser.add_argument("--gateway-api-port", type=int, default=8091)
    run_parser.add_argument("--tm-ingest-port", type=int, default=51000)
    run_parser.add_argument("--tc-from-yamcs-port", type=int, default=50001)
    run_parser.add_argument("--yamcs-tm-host", default="127.0.0.1")
    run_parser.add_argument("--dedup-window", type=float, default=1.5)

    subparsers.add_parser("stop")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    check_parser.add_argument("--yamcs-url", default="http://localhost:8090")
    check_parser.add_argument("--grafana-url", default="http://localhost:3000")
    check_parser.add_argument("--timeout", type=int, default=180)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "stop":
        stop()
        return 0
    if args.command == "check":
        return check_server(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
