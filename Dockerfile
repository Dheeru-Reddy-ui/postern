# Two images from one Dockerfile: `--target api` and `--target worker`.
#
# Only the worker image contains LiteLLM and the BigQuery client (the `worker` extra), so
# postern-api, which holds the OAuth client secret and the token-encryption key, carries no
# LLM code at all (TM-28, ADR-012).
#
# Dependencies live in their own layer, which changes only when uv.lock does; each commit
# adds only the source tree. That keeps Artifact Registry inside its 0.5 GB free tier (TM-12).
# Base images are pinned by digest (TM-28). Bump them deliberately.

FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./

FROM build AS deps-api
RUN uv sync --locked --no-dev --no-install-project

FROM build AS deps-worker
RUN uv sync --locked --no-dev --extra worker --no-install-project

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home postern
WORKDIR /app
USER 10001
# `python -m postern` imports the package from /app, the working directory.
ENTRYPOINT ["python", "-m", "postern"]

FROM runtime AS worker
ENV POSTERN_COMPONENT=worker
COPY --from=deps-worker --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 postern ./postern
ARG GIT_SHA=unknown
ENV POSTERN_GIT_SHA=${GIT_SHA}

FROM runtime AS api
ENV POSTERN_COMPONENT=api
COPY --from=deps-api --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 postern ./postern
ARG GIT_SHA=unknown
ENV POSTERN_GIT_SHA=${GIT_SHA}
