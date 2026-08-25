# Simulated full-stack e2e

Build and run [fprime-yamcs-reference](https://github.com/fprime-community/fprime-yamcs-reference)
(default tag `v0.1.0`), bridge its `Drv.Udp` CCSDS frames to the PROVES GS client
over TCP, and exercise the central Yamcs gateway.

```text
F´ YamcsDeployment (UDP :52000/:52001)
        ↕
udp_tcp_bridge.py (TCP :5000)
        ↕
proves-gs-client --skip-auth
        ↕
gateway (:51000 TM / :50001 TC / :8091)
        ↕
Yamcs Docker (:8090 / :50000)
```

`--skip-auth` is required because stock ComCcsds does not use the PROVES HMAC
telecommand wrapper.

## Local run

```sh
./scripts/run_e2e_sim.sh
```

Artifacts land under `.e2e/` (gitignored).
