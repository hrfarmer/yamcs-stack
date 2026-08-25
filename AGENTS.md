# AGENTS

## Cursor Cloud specific instructions

The repo is the **PROVES Yamcs stack**, a spacecraft ground system split into two
`uv`-managed Python packages plus a simulation harness:

- `server/` — central Yamcs (runs in Docker, web UI on `:8090`), Grafana with
  the JAOPS Yamcs plugin (`:3000`), the F´ event bridge, and the
  multi-ground-station gateway (API/UI on `:8091`).
- `client/` — the ground-station radio passthrough client.
- `sim/` — full-stack e2e harness (`scripts/run_e2e_sim.sh`) and the
  long-running test stack (`scripts/run_test_sim.sh`).

Standard lint/test/build/run commands live in `server/Makefile` and
`client/Makefile` (`make help` lists targets); CI is `.github/workflows/ci.yml`.
Notes below cover only non-obvious setup/run caveats.

### Toolchain
- Both packages target **Python 3.13**. Each Makefile vendors a pinned `uv` under
  `<pkg>/.tools/` and `uv` auto-provisions the 3.13 interpreter, so the system
  `python3` (3.12) is irrelevant — always drive things through the `make`
  targets, not a global `python`/`uv`.
- The dependency-refresh update script only installs Python deps and renders the
  Yamcs runtime config (`server: make setup-python && make prepare
  DEPLOYMENTS=tests/fixtures/deployments.toml`, `client: make setup`). Docker
  and service startup are intentionally NOT in it (see below).

### Docker / Yamcs (required for the server stack)
- Yamcs runs only in Docker. Docker is **not preinstalled** on the base VM; the
  committed `.cursor/environment.json` `start` step installs Docker CE, writes a
  Firecracker-compatible `/etc/docker/daemon.json`, starts `dockerd`, builds the
  Yamcs image, and boots the container. If you ever need to set Docker up by
  hand, replicate that: storage driver `fuse-overlayfs`,
  `features.containerd-snapshotter` set to `false` (needed on Docker 29+ so
  fuse-overlayfs is actually used), and switch `iptables`/`ip6tables` to their
  `-legacy` alternatives. Then run `sudo dockerd` (leave it running, e.g. in a
  tmux session) and `sudo chmod 666 /var/run/docker.sock` so the `docker` CLI
  works without sudo (the Makefiles call `docker` directly). The plain
  distro `docker.io` package + default overlay2 driver does **not** work on this
  kernel, so keep the `fuse-overlayfs` config.
- The Yamcs image (`proves-yamcs:5.12.8`) is built from `server/Dockerfile` via a
  Maven builder stage: `cd server && make docker-image`. No host Maven/JDK needed.
  Grafana uses `grafana/grafana:13.0.7` from Docker Hub and installs
  `jaops-yamcs-app` on first start (`GF_INSTALL_PLUGINS`).
- After `make prepare`, Docker Compose must include the generated UDP overlay:
  `compose.yaml` plus `runtime/compose.udp.yaml` (the Makefiles and supervisor
  pass both automatically).

### Running and testing
- Boot the whole server stack (Yamcs + Grafana + gateway + event bridge) with
  `cd server && make yamcs DEPLOYMENTS=tests/fixtures/deployments.toml`. It runs
  in the foreground as a supervisor; stop it with `make yamcs-stop`. Yamcs web
  UI is `http://localhost:8090` (no auth in dev; instance selector picks the
  satellite), Grafana `http://localhost:3000` (anonymous Admin, JAOPS Yamcs
  plugin, one folder per F´ deployment), gateway UI
  `http://localhost:8091`.
- Because there is no committed flight dictionary, always pass
  `DEPLOYMENTS=tests/fixtures/deployments.toml` to `make prepare` / `make yamcs*`
  targets (matches CI); the default `config/deployments.toml` points at
  `inputs/proves`, which is empty in this repo.
- Each `[[deployment]]` in the TOML becomes one Yamcs instance (own XTCE, SCID,
  TM UDP port) and one Grafana folder (Overview + Commanding layouts, plus any
  extra JSON under `server/grafana/dashboards/<name>/`). The gateway routes TM
  by CCSDS spacecraft ID.
- `make test` runs unit tests only (`tests/unit`). The server `tests/integration`
  suite and `scripts/run_e2e_sim.sh` require a live/simulated spacecraft (the e2e
  script builds `fprime-yamcs-reference` in C++), so they will not pass against a
  bare Yamcs boot.
- To exercise Yamcs + Grafana + the gateway without a PROVES board, run
  `./scripts/run_test_sim.sh` (needs `git`, `docker`, `python3` + `python3-venv`,
  GNU `g++`/`gcc` rather than a clang `/usr/bin/c++`, and `cmake`). It is the
  e2e sim kept alive until Ctrl+C, with two example F´ deployments
  (`proves-flight` and `proves-engineering`) so Yamcs instance switching and
  per-deployment Grafana folders can be exercised locally.
- Expected without a ground-station client: the gateway reports `tc_dropped`
  (not `tc_forwarded`) when a telecommand is issued, because no active TX station
  is registered. The command still traverses Yamcs → `UDP_TC_OUT` → gateway.
