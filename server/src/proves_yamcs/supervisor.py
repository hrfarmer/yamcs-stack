"""Own the central Yamcs container, event bridge, and multi-GS gateway."""

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
from pathlib import Path

PID_FILE = Path("runtime/pids/supervisor.json")


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
    return subprocess.run([*command, *args], check=check)


def _http_json(url: str) -> dict | list:
    with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310
        return json.load(response)


def wait_until_ready(url: str, instance: str, timeout: int = 180) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            state = _http_json(f"{url}/api/instances/{instance}")
            if isinstance(state, dict) and state.get("state") == "RUNNING":
                print(f"Yamcs instance {instance} is RUNNING")
                return
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(1)
    raise TimeoutError(
        f"Yamcs instance {instance} did not become ready within {timeout}s: "
        f"{last_error}"
    )


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


def run(args: argparse.Namespace) -> int:
    stop()
    _compose("up", "--build", "-d", "yamcs")
    wait_until_ready(args.yamcs_url, args.instance, args.timeout)

    dictionary = (args.input_dir / "fprime-dictionary.json").resolve()
    event_command = [
        str(Path(sys.executable).with_name("fprime-yamcs-events")),
        "--yamcs-url",
        args.yamcs_url,
        "--instance",
        args.instance,
        "--dictionary",
        str(dictionary),
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
        "--yamcs-tm-port",
        str(args.yamcs_tm_port),
        "--yamcs-url",
        args.yamcs_url,
        "--yamcs-instance",
        args.instance,
        "--dedup-window",
        str(args.dedup_window),
    ]

    event_process = subprocess.Popen(event_command, start_new_session=True)
    gateway_process = subprocess.Popen(gateway_command, start_new_session=True)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(
        json.dumps(
            {
                "supervisor": os.getpid(),
                "events": event_process.pid,
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
            event_status = event_process.poll()
            if gateway_status is not None:
                return gateway_status
            if event_status is not None:
                raise RuntimeError(
                    f"event bridge exited unexpectedly with {event_status}"
                )
            time.sleep(0.25)
    finally:
        _terminate(gateway_process)
        _terminate(event_process)
        _compose("down", "--remove-orphans", check=False)
        PID_FILE.unlink(missing_ok=True)
    return 0


def check_server(args: argparse.Namespace) -> int:
    _compose("down", "--remove-orphans", check=False)
    try:
        _compose("up", "--build", "-d", "yamcs")
        wait_until_ready(args.yamcs_url, args.instance, args.timeout)
        server = _http_json(f"{args.yamcs_url}/api")
        if not isinstance(server, dict) or not server.get("yamcsVersion"):
            raise RuntimeError("Yamcs server metadata did not include a version")
        commands = _http_json(
            f"{args.yamcs_url}/api/mdb/{args.instance}/commands?limit=1"
        )
        parameters = _http_json(
            f"{args.yamcs_url}/api/mdb/{args.instance}/parameters?limit=1"
        )
        links = _http_json(f"{args.yamcs_url}/api/links/{args.instance}")
        if not isinstance(commands, dict) or not commands.get("commands"):
            raise RuntimeError("Yamcs MDB did not expose any commands")
        if not isinstance(parameters, dict) or not parameters.get("parameters"):
            raise RuntimeError("Yamcs MDB did not expose any parameters")
        if not isinstance(links, dict):
            raise RuntimeError("Yamcs links endpoint returned an invalid response")
        link_states = {
            link.get("name"): link.get("status") for link in links.get("links", [])
        }
        unhealthy = [
            name
            for name in ("UDP_TM_IN", "UDP_TC_OUT")
            if link_states.get(name) != "OK"
        ]
        if unhealthy:
            raise RuntimeError(f"Yamcs links are not healthy: {', '.join(unhealthy)}")
        archive_root = Path("runtime/data")
        archive_identities = {
            path.relative_to(archive_root): path.read_bytes()
            for path in archive_root.glob("*.rdb/IDENTITY")
        }
        if Path(f"{args.instance}.rdb/IDENTITY") not in archive_identities:
            raise RuntimeError("Yamcs did not create persistent archive data")

        _compose("restart", "yamcs")
        wait_until_ready(args.yamcs_url, args.instance, args.timeout)
        if any(
            not (archive_root / path).is_file()
            or (archive_root / path).read_bytes() != identity
            for path, identity in archive_identities.items()
        ):
            raise RuntimeError("Yamcs archive data did not survive container restart")
        print(f"Yamcs {server['yamcsVersion']} build check passed")
        return 0
    finally:
        _compose("down", "--remove-orphans", check=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input-dir", type=Path, default=Path("inputs/proves"))
    run_parser.add_argument("--yamcs-url", default="http://localhost:8090")
    run_parser.add_argument("--instance", default="fprime-project")
    run_parser.add_argument("--timeout", type=int, default=180)
    run_parser.add_argument("--gateway-api-host", default="0.0.0.0")
    run_parser.add_argument("--gateway-api-port", type=int, default=8091)
    run_parser.add_argument("--tm-ingest-port", type=int, default=51000)
    run_parser.add_argument("--tc-from-yamcs-port", type=int, default=50001)
    run_parser.add_argument("--yamcs-tm-host", default="127.0.0.1")
    run_parser.add_argument("--yamcs-tm-port", type=int, default=50000)
    run_parser.add_argument("--dedup-window", type=float, default=1.5)

    subparsers.add_parser("stop")
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--yamcs-url", default="http://localhost:8090")
    check_parser.add_argument("--instance", default="fprime-project")
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
