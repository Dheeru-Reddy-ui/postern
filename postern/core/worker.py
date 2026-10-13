"""postern-worker entrypoint (Cloud Run Job). Phase 1 proves the deploy pipeline only.

Phase 3 replaces the body with: dequeue, policy gate, agent loop, checkpoints.
"""

import json
import os
import sys
from datetime import UTC, datetime


def main() -> int:
    record = {
        "severity": "INFO",
        "component": "worker",
        "message": "postern-worker hello",
        "git_sha": os.environ.get("POSTERN_GIT_SHA", "unknown"),
        "execution": os.environ.get("CLOUD_RUN_EXECUTION", "local"),
        "time": datetime.now(UTC).isoformat(),
    }
    # Cloud Logging parses one JSON object per stdout line into a structured entry.
    print(json.dumps(record), file=sys.stdout, flush=True)
    return 0
