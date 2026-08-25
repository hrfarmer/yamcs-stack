"""End-to-end command/event round-trip through Yamcs and a connected spacecraft."""

from __future__ import annotations

import os
import queue
import time

NO_OP_EVENT_NEEDLE = os.environ.get("E2E_NO_OP_EVENT", "NoOpReceived")
TOTAL_TIMEOUT_S = float(os.environ.get("E2E_TIMEOUT_S", "180"))
RETRY_INTERVAL_S = float(os.environ.get("E2E_RETRY_INTERVAL_S", "10"))


def _event_matches(event) -> bool:
    return any(
        NO_OP_EVENT_NEEDLE in value
        for attribute in ("event_type", "message", "source")
        if (value := getattr(event, attribute, None))
    )


def _resolve_noop_command(yamcs_client, yamcs_instance: str) -> str:
    explicit = os.environ.get("E2E_NO_OP_COMMAND")
    if explicit:
        return explicit
    matches = [
        command.qualified_name
        for command in yamcs_client.get_mdb(yamcs_instance).list_commands()
        if command.qualified_name.endswith("CMD_NO_OP") or command.name == "CMD_NO_OP"
    ]
    if not matches:
        raise AssertionError("MDB does not contain a CMD_NO_OP command")
    matches.sort(key=len)
    return matches[0]


def test_noop_round_trip(yamcs_client, yamcs_processor, yamcs_instance):
    command = _resolve_noop_command(yamcs_client, yamcs_instance)
    events: queue.Queue = queue.Queue()
    subscription = yamcs_client.create_event_subscription(
        instance=yamcs_instance,
        on_data=events.put,
    )
    try:
        time.sleep(0.5)
        while not events.empty():
            events.get_nowait()
        deadline = time.time() + TOTAL_TIMEOUT_S
        attempts = 0
        next_issue = 0.0
        while time.time() < deadline:
            if time.time() >= next_issue:
                attempts += 1
                yamcs_processor.issue_command(command)
                next_issue = time.time() + RETRY_INTERVAL_S
            try:
                event = events.get(timeout=1.0)
            except queue.Empty:
                continue
            if _event_matches(event):
                return
        raise AssertionError(
            f"Did not observe {NO_OP_EVENT_NEEDLE!r} for {command} within "
            f"{TOTAL_TIMEOUT_S}s after {attempts} attempts"
        )
    finally:
        subscription.cancel()
