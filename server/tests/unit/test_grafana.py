import json
from pathlib import Path

from proves_yamcs.grafana import dashboard_uid, render_grafana
from tests.unit.test_prepare import resolved


def test_dashboard_uid_stays_within_grafana_limit():
    uid = dashboard_uid("very-long-deployment-name-for-grafana", "commanding")
    assert len(uid) <= 40
    assert uid.replace("-", "").isalnum()


def test_render_grafana_copies_extra_layout_into_deployment_folder(tmp_path: Path):
    grafana_dir = tmp_path / "grafana"
    templates = grafana_dir / "templates"
    extras = grafana_dir / "dashboards" / "sat-a"
    templates.mkdir(parents=True)
    extras.mkdir(parents=True)
    (templates / "overview.json").write_text(
        json.dumps(
            {
                "uid": "__DASHBOARD_UID__",
                "title": "__DEPLOYMENT__ Overview",
                "endpoint": "__ENDPOINT__",
            }
        ),
        encoding="utf-8",
    )
    (extras / "payload.json").write_text(
        json.dumps(
            {
                "uid": "__DASHBOARD_UID__",
                "title": "__DEPLOYMENT__ Payload",
                "endpoint": "__ENDPOINT__",
            }
        ),
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    deployments = [
        resolved(tmp_path / "a", name="sat-a", spacecraft_id=68, tm_port=50000),
        resolved(tmp_path / "b", name="sat-b", spacecraft_id=67, tm_port=50002),
    ]
    output = render_grafana(deployments, grafana_dir, runtime)
    payload = json.loads(
        (output / "dashboards/sat-a/payload.json").read_text(encoding="utf-8")
    )
    assert payload["uid"] == "yamcs-sat-a-payload"
    assert payload["title"] == "sat-a Payload"
    assert payload["endpoint"] == "sat-a_realtime"
    assert not (output / "dashboards/sat-b/payload.json").is_file()
    datasource = (output / "datasources/yamcs.yaml").read_text(encoding="utf-8")
    assert '"sat-a_realtime"' in datasource
    assert '"sat-b_replay"' in datasource
    assert (output / "home.json").is_file()
