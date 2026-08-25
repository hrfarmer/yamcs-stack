"""Fixtures for the manually invoked live-board Yamcs test."""

import json
import os
import time
from pathlib import Path

import pytest
from yamcs.client import YamcsClient

YAMCS_URL = os.environ.get("YAMCS_URL", "http://localhost:8090")
YAMCS_CLIENT_URL = YAMCS_URL.removeprefix("http://").removeprefix("https://")
YAMCS_PROCESSOR = os.environ.get("YAMCS_PROCESSOR", "realtime")
YAMCS_READY_TIMEOUT_S = float(os.environ.get("YAMCS_READY_TIMEOUT_S", "180"))
MANIFEST_PATH = Path("runtime/config/deployments.json")


def resolve_yamcs_instance() -> str:
    if instance := os.environ.get("YAMCS_INSTANCE"):
        return instance
    if MANIFEST_PATH.is_file():
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        deployments = payload.get("deployments") or []
        if deployments:
            return deployments[0]["instance"]
    pytest.fail(
        "YAMCS_INSTANCE is not set and runtime/config/deployments.json is missing"
    )


@pytest.fixture(scope="session")
def yamcs_instance():
    return resolve_yamcs_instance()


@pytest.fixture(scope="session")
def yamcs_client(yamcs_instance):
    client = YamcsClient(YAMCS_CLIENT_URL)
    deadline = time.time() + YAMCS_READY_TIMEOUT_S
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            instance = next(
                (
                    item
                    for item in client.list_instances()
                    if item.name == yamcs_instance
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
        f"Yamcs instance {yamcs_instance!r} did not become ready at {YAMCS_URL} "
        f"within {YAMCS_READY_TIMEOUT_S}s; last error: {last_error!r}"
    )


@pytest.fixture(scope="session")
def yamcs_processor(yamcs_client, yamcs_instance):
    return yamcs_client.get_processor(
        instance=yamcs_instance,
        processor=YAMCS_PROCESSOR,
    )
