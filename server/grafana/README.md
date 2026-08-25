# Grafana (JAOPS Yamcs plugin)

Grafana runs next to Yamcs in `compose.yaml` and is provisioned with the
[JAOPS Yamcs app](https://grafana.com/grafana/plugins/jaops-yamcs-app/).

- URL: <http://localhost:3000>
- Auth: anonymous Admin (same development posture as Yamcs). Default login
  `admin` / `admin` is also available if the login form is re-enabled.
- Datasource: `JAOPS Yamcs` → `yamcs:8090` / instance `proves-flight`
  (matches `config/deployments.toml`; add endpoints for extra `[[deployment]]`
  names if you host more than one satellite)
- Home dashboard: **PROVES Yamcs Overview** (link rates, JVM, command history,
  events)

The plugin talks to Yamcs from inside the Grafana container, so the provisioned
host path is the Compose service name `yamcs:8090`, not `localhost:8090`.
