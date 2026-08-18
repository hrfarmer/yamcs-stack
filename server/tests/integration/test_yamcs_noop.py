"""End-to-end command/event round-trip through Yamcs and a live spacecraft."""

import queue
import time

NO_OP_COMMAND = "/ReferenceDeployment_ReferenceDeployment/CdhCore/cmdDisp/CMD_NO_OP"
NO_OP_EVENT_NEEDLE = "NoOpReceived"
TOTAL_TIMEOUT_S = 180.0
RETRY_INTERVAL_S = 10.0


def _event_matches(event) -> bool:
    return any(
        NO_OP_EVENT_NEEDLE in value
        for attribute in ("event_type", "message", "source")
        if (value := getattr(event, attribute, None))
    )


def test_noop_round_trip(yamcs_client, yamcs_processor, yamcs_instance):
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
                yamcs_processor.issue_command(NO_OP_COMMAND)
                next_issue = time.time() + RETRY_INTERVAL_S
            try:
                event = events.get(timeout=1.0)
            except queue.Empty:
                continue
            if _event_matches(event):
                return
        raise AssertionError(
            f"Did not observe {NO_OP_EVENT_NEEDLE!r} within {TOTAL_TIMEOUT_S}s "
            f"after {attempts} CMD_NO_OP attempts"
        )
    finally:
        subscription.cancel()
