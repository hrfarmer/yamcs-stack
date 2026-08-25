# Set up the PROVES Yamcs stack

## What you are setting up

The stack is two programs that talk to each other:

```text
 radio board ──► GS client (HMAC + CCSDS) ──Tailscale──► gateway ──► Yamcs
                       ▲                                  │
                       └──────── TC (selected station) ───┘
```

| Piece | Where it runs | What it does |
| --- | --- | --- |
| **Yamcs** | Central host, in Docker | Mission database, telemetry display, command UI (`:8090`) |
| **Gateway** | Central host, next to Yamcs | Accepts TM from every station, deduplicates it, and sends TC to one chosen station (`:8091`) |
| **GS client** | Each ground station | Talks to the local radio (UART or TCP) and forwards frames to the gateway |

Several stations can stream telemetry at once. Telecommands go through **one**
station at a time: the *active TX station*.

Development posture: Yamcs and the gateway have **no operator login or TLS**.
Reachability is meant to come from a Tailscale network, not from the public
Internet. Do not publish ports `8090`, `8091`, `51000/udp`, or `50001/udp`
beyond that overlay.

## Choose your machines

You typically need:

1. **One central host** — always-on Linux box with Docker. This is where Yamcs
   and the gateway run.
2. **One or more ground-station hosts** — the machines that have a radio
   board (USB serial) or a TCP bent-pipe to firmware.

The same laptop can play both roles for a local dry run. Production sites
usually split them and join them with Tailscale.

## Prerequisites

### On the central host

- Linux, `git`, `curl`, and a working **Docker Engine** with the Compose
  plugin (`docker compose version` should succeed).
- Permission to talk to the Docker socket (`docker info` works without
  extra fuss).
- Outbound HTTPS so `make` can download a pinned [`uv`](https://docs.astral.sh/uv/)
  binary. `uv` then provisions **Python 3.13** itself. The system `python3`
  does not need to be 3.13; always use the `make` targets, not a global
  `python` or `uv`.
- Enough disk for the Yamcs image (`proves-yamcs:5.12.8`). The first
  `make setup` compiles Yamcs inside Docker with Maven and can take several
  minutes. No host JDK or Maven install is required.

### On each ground station

- Linux, `git`, `curl` (same `uv` / Python 3.13 story as the server).
- Serial access to the radio if you are using UART, typically
  `/dev/ttyUSB0` or `/dev/ttyACM0`. On Debian/Ubuntu that usually means
  membership in the `dialout` group, then a new login:
  `sudo usermod -aG dialout "$USER"`.
- Docker is **not** required on ground stations.

### Network (real sites)

Install [Tailscale](https://tailscale.com/) on the central host and every
ground station, and join the same tailnet. Note:

- The **MagicDNS name** or Tailscale IPv4 of the central host (used as
  `server_host` in the client config).
- Each station’s Tailscale IPv4. The client usually auto-detects this; if
  telecommands never arrive, set `tc_advertise_host` to that IPv4 explicitly.

### Firewall (real sites)

| Direction | Port | Purpose |
| --- | --- | --- |
| Into the central host | TCP `8090` | Yamcs web UI / API |
| Into the central host | TCP `8091` | Gateway UI / station heartbeats |
| Into the central host | UDP `51000` | Telemetry from GS clients |
| Into each GS client | UDP `50001` | Telecommands from the gateway |

Keep those ports on the Tailscale interface only.

### Mission files you must obtain

This repo does **not** ship a flight dictionary. Both packages look for a
small *input bundle* under `inputs/proves/` (gitignored, so you copy files
in by hand).

| File | Central host | Ground station |
| --- | --- | --- |
| `fprime-dictionary.json` | **Required.** Yamcs is generated from it. | **Required.** Frame length and spacecraft ID come from it. |
| `auth-key.hex` | Optional (TC auth does not run on the server). | **Required** for a real radio. 32 hex characters (16 bytes). Never commit it. |

Export both from the matching flight-software tree. Existing docs in this
repo use `proves-core-reference`:

```sh
cd ~/code/spacelab/proves-core-reference   # or your local checkout
make yamcs-export
```

Use the **same** dictionary (and the same auth key) on the server and every
client. A mismatch means frames will not decode, or telecommands will be
rejected by the spacecraft.

The committed copies under `server/tests/fixtures/proves/` and
`client/tests/fixtures/proves/` are only for CI and local smoke tests. Do
not use them against real flight hardware.

---

## 1. Clone the stack

On every machine, clone this repository and `cd` into it. The rest of this
guide assumes that checkout.

---

## 2. Central host: install and boot Yamcs

### 2.1 Install Python deps and build the Yamcs image

```sh
cd server
make setup
```

This:

- downloads pinned `uv` into `server/.tools/`
- creates `server/.venv` with Python 3.13
- patches a known `fprime-yamcs` event-bridge issue
- builds Docker image `proves-yamcs:5.12.8`

Confirm Docker is healthy if this fails (`docker info`,
`docker compose version`).

### 2.2 Install the dictionary

```sh
mkdir -p inputs/proves
cp /path/to/export/fprime-dictionary.json inputs/proves/
# optional on the server:
# cp /path/to/export/auth-key.hex inputs/proves/
```

`make yamcs` reads `inputs/proves` by default. If you only want to prove the
server boots and have no flight export yet, skip the copy and pass
`INPUT_DIR=tests/fixtures/proves` to the start command in the next step.

### 2.3 Start the stack

```sh
make yamcs
# or, with the test dictionary only:
# make yamcs INPUT_DIR=tests/fixtures/proves
```

That command stays in the **foreground**. It:

1. Renders Yamcs config and the XTCE mission database into `server/runtime/`
2. Starts the Yamcs container
3. Starts the F Prime event bridge
4. Starts the multi-station gateway

Leave that terminal open. In another terminal:

```sh
cd server
make yamcs-stop
```

stops the supervisor, gateway, event bridge, and this project's containers.

Open:

- Yamcs: [http://localhost:8090](http://localhost:8090) (or
  `http://<server-tailscale-name>:8090` from another machine)
- Ground-station panel: [http://localhost:8091](http://localhost:8091)

You should see the Yamcs UI and an empty “Ground Stations” table. That is
expected until a client heartbeats.

`make help` lists every server target. `make yamcs-server` starts only the
Yamcs container (no gateway / event bridge).

---

## 3. Ground station: install the client

Repeat on each station.

### 3.1 Install Python deps

```sh
cd client
make setup
```

### 3.2 Install the matching bundle

```sh
mkdir -p inputs/proves
cp /path/to/export/fprime-dictionary.json inputs/proves/
cp /path/to/export/auth-key.hex inputs/proves/
```

The auth key stays on the ground station. It is used to wrap telecommands
before they go to the radio.

### 3.3 Write a site config

Configs live next to the examples and are gitignored as `config/gs.toml`.

**UART radio (typical lab / site):**

```sh
cp config/gs.serial.example.toml config/gs.toml
```

Edit at least:

```toml
mode = "serial"
input_dir = "inputs/proves"
server_host = "yamcs-server"   # Tailscale MagicDNS name or 100.x IPv4
station_name = "gs-lab"        # unique per station; shown in the gateway UI
uart_device = "/dev/ttyUSB0"
uart_baud = 115200
```

**TCP bent-pipe** (firmware or a simulator speaking CCSDS frames on a TCP
socket, no UART):

```sh
cp config/gs.tcp.example.toml config/gs.toml
```

Then set `tcp_host` / `tcp_port` as well as `server_host` and
`station_name`.

Useful optional keys (uncomment in the example files):

| Key | When to set it |
| --- | --- |
| `tc_advertise_host` | Auto-detect picked the wrong NIC. Use the station’s Tailscale IPv4. |
| `gateway_api_url` | Gateway is not at `http://<server_host>:8091`. |
| `skip_auth` | **Simulation only.** Stock F Prime ComCcsds has no PROVES HMAC wrapper. Leave `false` for real radios. |

CLI flags (`--server-host`, `--uart-device`, …) override individual keys for
one-off debugging. Prefer editing the TOML for normal operation.

### 3.4 Run the client

```sh
make run
```

(`CONFIG=config/gs.toml` is the default.) You should see log lines for:

- opening the UART (or TCP connection)
- TM forwarding to `server_host:51000`
- heartbeats to `http://<server_host>:8091/api/stations/<name>/heartbeat`
- the TC address being advertised (`[station] name=… advertise TC to …`)

Leave this process running while the station is in use.

---

## 4. Select a transmit station and check the path

1. Open the gateway UI at `http://<server>:8091`. Each running
   client should appear **online**.
2. Choose that station in the dropdown and click **Apply**, **or** set the
   Yamcs parameter `/Ground/ActiveTxStation` (Parameters → set value).
3. In Yamcs (`:8090`), open the instance `fprime-project`. Telemetry
   parameters should start updating once the radio (or bent-pipe) is
   producing frames.
4. Issue a harmless command such as `CMD_NO_OP`. The gateway page shows
   `TC forwarded` when a selected online station received it.

Without an active TX station, a command still leaves Yamcs and hits the
gateway, but the gateway increments `tc_dropped` instead of
`tc_forwarded`. That is expected.

---

## 5. Confirm a first-time install (checklist)

On the **server**:

- [ ] `docker info` works
- [ ] `make setup` finished without error
- [ ] `inputs/proves/fprime-dictionary.json` is the flight export (or you
      passed `INPUT_DIR=tests/fixtures/proves` on purpose)
- [ ] `make yamcs` is running in a terminal
- [ ] [http://localhost:8090](http://localhost:8090) loads Yamcs
- [ ] [http://localhost:8091](http://localhost:8091) loads the station list

On each **ground station**:

- [ ] `make setup` finished
- [ ] Dictionary **matches** the server
- [ ] `auth-key.hex` is present (unless this is a `skip_auth` sim)
- [ ] `config/gs.toml` has a unique `station_name` and the real
      `server_host`
- [ ] `make run` is up; heartbeats succeed (no repeating
      `[register] heartbeat failed`)
- [ ] The station is **online** on `:8091`
- [ ] That station is the active TX if you intend to command through it

---

## Same-machine dry run (no Tailscale, no radio)

Useful when you only want to prove the server boots:

```sh
cd server
make setup
make yamcs INPUT_DIR=tests/fixtures/proves
```

Yamcs and the gateway come up; the station table stays empty. The committed
end-to-end simulator (builds reference F Prime C++, needs extra packages)
is documented in [`sim/README.md`](sim/README.md):

```sh
./scripts/run_e2e_sim.sh
```

---

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `make setup` / `make yamcs` says Docker is missing or the daemon is down | Install Docker Engine + Compose; start the daemon; confirm `docker info`. |
| `dictionary is missing` | Copy `fprime-dictionary.json` into `inputs/proves/`, or pass `INPUT_DIR=…`. |
| Yamcs UI never loads | Wait for `Yamcs instance fprime-project is RUNNING` in the supervisor terminal; `make yamcs` also validates generated config first. |
| Station never appears / heartbeat failed | `server_host` must be reachable on TCP `8091`. Try `curl http://<server>:8091/api/status` from the GS host. |
| Station is online but TC is dropped | Select it as active TX. Confirm `tc_advertise_host` is an address the **server** can send UDP to (usually the station Tailscale IPv4). |
| TM never shows in Yamcs | Client log should count TM frames. Gateway `:8091` should show `TM accepted` rising. Dictionary spacecraft ID / frame length must match the radio. |
| Serial open fails | Device path, baud, and `dialout` (or equivalent) permissions. Unplug other serial monitors. |
| Client refuses to start without a key | Place `auth-key.hex` in `inputs/proves/`, or set `skip_auth = true` only for a simulator that does not use PROVES HMAC. |

Useful URLs and files:

- Gateway JSON: `http://<server>:8091/api/status`
- Generated Yamcs config: `server/runtime/config/`
- Client sequence counter: `client/runtime/state/sequence-number` (created
  on first authenticated TC)

---

## Where to read next

- [`README.md`](README.md) — short architecture summary
- [`server/README.md`](server/README.md) — ports and TX-station selection
- [`client/README.md`](client/README.md) — client responsibilities and CLI
- [`server/inputs/proves/README.md`](server/inputs/proves/README.md) and
  [`client/inputs/proves/README.md`](client/inputs/proves/README.md) —
  bundle file contract
- `cd server && make help` / `cd client && make help` — every Make target
