from proves_yamcs.gateway import FrameDeduper, Gateway


def test_deduper_drops_identical_frames_inside_window():
    deduper = FrameDeduper(1.0)
    frame = b"\x01" * 16

    assert deduper.accept(frame) is True
    assert deduper.accept(frame) is False
    assert deduper.accepted == 1
    assert deduper.duplicates == 1


def test_gateway_registers_and_selects_active_tx(tmp_path):
    gateway = Gateway(
        yamcs_tm_host="127.0.0.1",
        yamcs_tm_port=50000,
        yamcs_url="http://127.0.0.1:8090",
        yamcs_instance="fprime-project",
        stale_after=30.0,
        dedup_window=1.0,
    )
    gateway.heartbeat("gs-a", "100.64.0.1", 50001)
    gateway.heartbeat("gs-b", "100.64.0.2", 50001)

    assert gateway.get_active_tx() == "gs-a"
    gateway.set_active_tx("gs-b", sync_yamcs=False)
    assert gateway.get_active_tx() == "gs-b"

    names = {station["name"] for station in gateway.list_stations()}
    assert names == {"gs-a", "gs-b"}
