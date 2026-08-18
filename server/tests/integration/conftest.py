"""Fixtures for the manually invoked live-board Yamcs test."""

import os
import time

import pytest
from yamcs.client import YamcsClient

YAMCS_URL = os.environ.get("YAMCS_URL", "http://localhost:8090")
YAMCS_CLIENT_URL = YAMCS_URL.removeprefix("http://").removeprefix("https://")
YAMCS_INSTANCE = os.environ.get("YAMCS_INSTANCE", "fprime-project")
YAMCS_PROCESSOR = os.environ.get("YAMCS_PROCESSOR", "realtime")
YAMCS_READY_TIMEOUT_S = float(os.environ.get("YAMCS_READY_TIMEOUT_S", "180"))


@pytest.fixture(scope="session")
def yamcs_instance():
    return YAMCS_INSTANCE


@pytest.fixture(scope="session")
def yamcs_client():
    client = YamcsClient(YAMCS_CLIENT_URL)
    deadline = time.time() + YAMCS_READY_TIMEOUT_S
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            instance = next(
                (
                    item
                    for item in client.list_instances()
                    if item.name == YAMCS_INSTANCE
                ),
                None,
            )
            if instance is not None and getattr(instance, "state", None) == "RUNNING":
                yield client
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(1)
    pytest.fail(
        f"Yamcs instance {YAMCS_INSTANCE!r} did not become ready at {YAMCS_URL} "
        f"within {YAMCS_READY_TIMEOUT_S}s; last error: {last_error!r}"
    )


@pytest.fixture(scope="session")
def yamcs_processor(yamcs_client):
    return yamcs_client.get_processor(
        instance=YAMCS_INSTANCE,
        processor=YAMCS_PROCESSOR,
    )
