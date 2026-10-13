# One image for every Postern component; POSTERN_COMPONENT picks api or worker at runtime.
# Base images are pinned by digest (threat model TM-28). Bump them deliberately.

FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first so code changes don't invalidate the dependency layer.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY postern ./postern
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ARG GIT_SHA=unknown
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    POSTERN_GIT_SHA=${GIT_SHA}
RUN useradd --system --uid 10001 --no-create-home postern
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
USER 10001
ENTRYPOINT ["python", "-m", "postern"]
