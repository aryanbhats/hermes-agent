#!/usr/bin/env python3
"""CLI wrapper for agent to call health_log functions via terminal tool.
Usage:
    HEALTH_LOG_CMD=log HEALTH_LOG_ARGS='{"type":"habit","subtype":"walk","value":25}' python health_log_cli.py
    HEALTH_LOG_CMD=last_interaction python health_log_cli.py
    HEALTH_LOG_CMD=today python health_log_cli.py
    HEALTH_LOG_CMD=range HEALTH_LOG_ARGS='{"days":7}' python health_log_cli.py
"""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from health_log import log_event, last_user_interaction, today_events, events_range

cmd = os.environ.get("HEALTH_LOG_CMD", "")
args = json.loads(os.environ.get("HEALTH_LOG_ARGS", "{}"))

if cmd == "log":
    try:
        log_event(**args)
        print("OK")
    except ValueError as e:
        print(f"VALIDATION ERROR: {e}", file=sys.stderr)
        sys.exit(1)
elif cmd == "last_interaction":
    print(last_user_interaction() or "NEVER")
elif cmd == "today":
    print(json.dumps(today_events(), indent=2))
elif cmd == "range":
    print(json.dumps(events_range(args.get("days", 7)), indent=2))
else:
    print(f"Unknown command: {cmd}", file=sys.stderr)
    sys.exit(1)
