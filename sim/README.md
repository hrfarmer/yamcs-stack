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
```

On a real Tailscale GS the client can keep TC on `:50001`; the e2e script uses
`:51001` so it does not collide with the gateway's Yamcs-TC socket on loopback.
`--skip-auth` is required because stock ComCcsds does not use the PROVES HMAC
telecommand wrapper.

## Local run

```sh
./scripts/run_e2e_sim.sh
```

Artifacts land under `.e2e/` (gitignored).
