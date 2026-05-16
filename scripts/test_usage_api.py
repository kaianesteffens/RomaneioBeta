from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from usage_reporter import report_usage_event


def main() -> int:
    result = report_usage_event("app_started", module="manual_test", status="ok", wait=True)
    sent = "true" if bool(result.get("sent")) else "false"
    print(f"sent: {sent}")
    status_code = result.get("status_code")
    if status_code is not None:
        print(f"status_code: {status_code}")
    event_id = result.get("id")
    if event_id:
        print(f"id: {event_id}")
    return 0 if bool(result.get("sent")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
