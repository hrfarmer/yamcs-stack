# Yamcs configuration

`deployments.toml` (next to this file, or another path passed as
`DEPLOYMENTS=`) lists the F´ deployments hosted by this Yamcs process. Each
`[[deployment]]` becomes one Yamcs instance with its own XTCE mission database,
spacecraft ID, and TM UDP port.

`make prepare` reads that file, substitutes CCSDS spacecraft IDs and frame
lengths into `etc/yamcs.instance.yaml.template`, generates one XTCE MDB per
deployment, writes `runtime/config/deployments.json`, emits
`runtime/compose.udp.yaml` so Docker publishes each TM port, and renders
Grafana datasource endpoints plus a dashboard folder per deployment under
`runtime/grafana/`.

Do not put secrets or generated mission databases in this directory.
