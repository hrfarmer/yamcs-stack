#!/usr/bin/env python3
"""Apply the fprime-yamcs-events busy-wait fix to its installed processor."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <processor.py>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    content = path.read_text(encoding="utf-8")
    if "subscription.result()" in content:
        print("fprime-yamcs-events CPU fix already applied")
        return 0
    fixed = re.sub(
        r"# Keep the script running\s*\n([ \t]*)while True:\s*\n\1[ \t]+pass",
        r"# Block until the WebSocket subscription ends\n\1subscription.result()",
        content,
    )
    if fixed == content:
        print(
            "expected fprime-yamcs-events busy-wait source was not found; "
            "verify the upstream package before changing this patch",
            file=sys.stderr,
        )
        return 1
    path.write_text(fixed, encoding="utf-8")
    print("applied fprime-yamcs-events CPU fix")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
