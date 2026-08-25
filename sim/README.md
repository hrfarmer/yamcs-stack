# Simulated full-stack e2e

Build and run [fprime-yamcs-reference](https://github.com/fprime-community/fprime-yamcs-reference)
(default tag `v0.1.0`), bridge its `Drv.Udp` CCSDS frames to the PROVES GS client
over TCP, and exercise the central Yamcs gateway.

```text
F´ YamcsDeployment (UDP :52000/:52001)
        ↕
udp_tcp_bridge.py (TCP :5000)
        ↕
proves-gs-client --skip-auth (TC listen :51001 on localhost)
        ↕
gateway (:51000 TM ingest / :50001 Yamcs-TC / :8091)
        ↕
Yamcs Docker (:8090 / :50000)
        ↕
Grafana Docker (:3000, JAOPS Yamcs plugin)
```

On a real Tailscale GS the client can keep TC on `:50001`; the e2e script uses
`:51001` so it does not collide with the gateway's Yamcs-TC socket on loopback.
`--skip-auth` / `skip_auth = true` in the generated client TOML is required
because stock ComCcsds does not use the PROVES HMAC telecommand wrapper.

## Local run

One-shot CI check (builds, round-trips `CMD_NO_OP`, then tears everything down):

```sh
./scripts/run_e2e_sim.sh
```

Leave the same stack running so Yamcs and Grafana can be exercised without a
PROVES board:

```sh
./scripts/run_test_sim.sh
# equivalent: ./scripts/run_e2e_sim.sh --keep-alive
```

URLs while it is up:

| URL | Role |
|-----|------|
| http://127.0.0.1:8090 | Yamcs web UI |
| http://127.0.0.1:3000 | Grafana (JAOPS Yamcs plugin, PROVES overview dashboard) |
| http://127.0.0.1:8091 | Gateway / ground-station panel |

Ctrl+C stops F´, the GS client, the gateway, Yamcs, and Grafana.

Artifacts land under `.e2e/` (gitignored). Pass `--skip-test` to skip the
`CMD_NO_OP` pytest after the stack is up.
