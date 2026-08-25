# Simulated full-stack e2e

Build and run [fprime-yamcs-reference](https://github.com/fprime-community/fprime-yamcs-reference)
(default tag `v0.1.0`), bridge its `Drv.Udp` CCSDS frames to the PROVES GS client
over TCP, and exercise the central Yamcs gateway with **two** F´ deployments.

```text
F´ proves-flight (UDP :52000/:52001, native SCID)
F´ proves-engineering (UDP :52010/:52011, wire SCID rewritten)
        ↕
udp_tcp_bridge.py (TCP :5000, routes TC by SCID)
        ↕
proves-gs-client --skip-auth (TC listen :51001 on localhost)
        ↕
gateway (:51000 TM ingest / :50001 Yamcs-TC / :8091)
        ↕
Yamcs Docker (:8090 / per-deployment TM UDP)
        ↕
Grafana Docker (:3000, one folder + layouts per deployment)
```

The reference F´ binary is compiled with a single spacecraft ID. The sim starts
two processes from that binary and rewrites CCSDS spacecraft ID (and CRC) on
the engineering instance so Yamcs, the gateway, and Grafana see two satellites.

On a real Tailscale GS the client can keep TC on `:50001`; the e2e script uses
`:51001` so it does not collide with the gateway's Yamcs-TC socket on loopback.
`skip_auth = true` on the generated `[[satellite]]` tables is required because
stock ComCcsds does not use the PROVES HMAC telecommand wrapper.

## Local run

One-shot CI check (builds, round-trips `CMD_NO_OP` on both instances, then tears
everything down):

```sh
./scripts/run_e2e_sim.sh
```

Leave the same two-deployment stack running so Yamcs and Grafana can be
exercised without a PROVES board:

```sh
./scripts/run_test_sim.sh
# equivalent: ./scripts/run_e2e_sim.sh --keep-alive
```

URLs while it is up:

| URL | Role |
|-----|------|
| http://127.0.0.1:8090 | Yamcs web UI (switch instance for each satellite) |
| http://127.0.0.1:3000 | Grafana (folders `proves-flight` / `proves-engineering`) |
| http://127.0.0.1:8091 | Gateway / ground-station panel |

Ctrl+C stops both F´ processes, the GS client, the gateway, Yamcs, and Grafana.

Artifacts land under `.e2e/` (gitignored). Pass `--skip-test` to skip the
`CMD_NO_OP` pytest after the stack is up.
