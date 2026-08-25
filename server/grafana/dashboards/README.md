# Extra dashboards per F´ deployment

`make prepare` copies every `templates/*.json` layout into a Grafana folder
named after the deployment (`runtime/grafana/dashboards/json/<name>/`). Drop
additional JSON files here to attach extra layouts to one satellite without
changing the shared templates:

```text
grafana/dashboards/<deployment-name>/payload-ops.json
```

Use the same placeholders as the templates (`__DEPLOYMENT__`, `__ENDPOINT__`,
`__DASHBOARD_UID__`, `__SCID__`, …) so the extra layout is retargeted to that
Yamcs instance.
