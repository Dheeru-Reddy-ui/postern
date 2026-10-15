"""postern-api: health, project connection (OAuth), and the worker's token broker."""

import os

from fastapi import FastAPI

from postern.api.connect import router as connect_router

app = FastAPI(title="postern-api", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(connect_router)


@app.get("/health")
def health() -> dict[str, str]:
    # Cloud Run reserves URL paths ending in "z", so this is /health, not /healthz.
    return {
        "status": "ok",
        "component": "api",
        "git_sha": os.environ.get("POSTERN_GIT_SHA", "unknown"),
    }
