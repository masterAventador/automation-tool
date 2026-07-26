# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Download the pinned artifacts from a path-identical mirror. The mirror serves
# the same /packages/<a>/<b>/<digest>/<file> layout, so only the hostname of the
# in-image copy of the lock changes; the committed uv.lock is untouched.
#
# This does NOT weaken dependency locking. `--locked` still refuses to resolve
# anything the lock does not already pin, and every artifact is still verified
# against the sha256 digest recorded in uv.lock, so a mirror can change how fast
# the bytes arrive but not which bytes are accepted.
#
# The host is a committed constant, not a build argument: every builder -- CI,
# workstation, cloud host -- resolves dependencies through the exact same path.
RUN sed -i "s|https://files\.pythonhosted\.org/|https://pypi.tuna.tsinghua.edu.cn/|g" uv.lock \
 && uv sync --locked --no-dev --no-group executor --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="automation-tool-control-plane" \
      org.opencontainers.image.description="Automation Tool customer Demo Control Plane" \
      org.opencontainers.image.source="https://github.com/masterAventador/automation-tool" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

RUN groupadd --gid 65532 automation-tool
RUN useradd --uid 65532 --gid 65532 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin automation-tool

ENV PATH="/app/.venv/bin:${PATH}" \
    AUTOMATION_TOOL_RUNTIME_SECRET_MODE=files \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=builder --chown=65532:65532 /app/.venv /app/.venv
COPY --chown=65532:65532 alembic.ini /app/alembic.ini
COPY --chown=65532:65532 migrations /app/migrations

USER 65532:65532
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2); assert response.status == 200; response.close()"]
ENTRYPOINT ["automation-tool-control-plane-container"]
