from proves_yamcs.deployments import RuntimeDeployment, RuntimeManifest
from proves_yamcs.gateway import FrameDeduper, Gateway, extract_tm_spacecraft_id


class RecordingSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        self.sent.append((data, address))


def tm_frame(spacecraft_id: int, length: int = 16) -> bytes:
    word = (spacecraft_id << 4) | (1 << 1)
    frame = bytearray(length)
    frame[0:2] = word.to_bytes(2, "big")
    return bytes(frame)


def manifest() -> RuntimeManifest:
    return RuntimeManifest(
        deployments=(
            RuntimeDeployment(
                name="sat-a",
                instance="sat-a",
                spacecraft_id=68,
                frame_length=248,
                tm_port=50000,
                tc_port=50001,
                dictionary="/tmp/a.json",
                mdb_file="sat-a.xtce.xml",
            ),
            RuntimeDeployment(
                name="sat-b",
                instance="sat-b",
                spacecraft_id=67,
                frame_length=248,
                tm_port=50002,
                tc_port=50001,
                dictionary="/tmp/b.json",
                mdb_file="sat-b.xtce.xml",
            ),
        )
    )


def make_gateway() -> Gateway:
    gateway = Gateway(
        yamcs_tm_host="127.0.0.1",
        manifest=manifest(),
        yamcs_url="http://127.0.0.1:8090",
        stale_after=30.0,
        dedup_window=1.0,
    )
    gateway._tm_out = RecordingSocket()  # noqa: SLF001
    return gateway


def test_deduper_drops_identical_frames_inside_window():
    deduper = FrameDeduper(1.0)
    frame = b"\x01" * 16

    assert deduper.accept(frame) is True
    assert deduper.accept(frame) is False
    assert deduper.accepted == 1
    assert deduper.duplicates == 1


def test_extract_tm_spacecraft_id():
    assert extract_tm_spacecraft_id(tm_frame(68)) == 68
    assert extract_tm_spacecraft_id(b"\x00") is None


def test_gateway_registers_and_selects_active_tx():
    gateway = make_gateway()
    gateway.heartbeat("gs-a", "100.64.0.1", 50001)
    gateway.heartbeat("gs-b", "100.64.0.2", 50001)

    assert gateway.get_active_tx() == "gs-a"
    gateway.set_active_tx("gs-b", sync_yamcs=False)
    assert gateway.get_active_tx() == "gs-b"

    names = {station["name"] for station in gateway.list_stations()}
    assert names == {"gs-a", "gs-b"}


def test_gateway_routes_tm_by_spacecraft_id():
    gateway = make_gateway()
    frame_a = tm_frame(68)
    frame_b = tm_frame(67)

    gateway.ingest_tm(frame_a, "100.64.0.1")
    gateway.ingest_tm(frame_b, "100.64.0.1")

    assert gateway._tm_out.sent == [  # noqa: SLF001
        (frame_a, ("127.0.0.1", 50000)),
        (frame_b, ("127.0.0.1", 50002)),
    ]
    status = gateway.status()
    by_name = {item["name"]: item for item in status["deployments"]}
    assert by_name["sat-a"]["tm_accepted"] == 1
    assert by_name["sat-b"]["tm_accepted"] == 1
    assert status["tm_unknown"] == 0


def test_gateway_drops_unknown_spacecraft_id():
    gateway = make_gateway()
    gateway.ingest_tm(tm_frame(99), "100.64.0.1")

    assert gateway._tm_out.sent == []  # noqa: SLF001
    assert gateway.status()["tm_unknown"] == 1
    assert gateway.status()["tm_accepted"] == 0
