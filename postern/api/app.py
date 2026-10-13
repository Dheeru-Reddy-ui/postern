"""postern-api: webhooks, OAuth callbacks and MCP transport. Phase 1 ships only /health."""

import os

from fastapi import FastAPI

app = FastAPI(title="postern-api", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict[str, str]:
    # Cloud Run reserves URL paths ending in "z", so this is /health, not /healthz.
    return {
        "status": "ok",
        "component": "api",
        "git_sha": os.environ.get("POSTERN_GIT_SHA", "unknown"),
    }
