import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-02 builds a locked production-only Control Plane image", async () => {
  const [dockerfile, dockerignore, pyproject] = await Promise.all([
    read("backend/Dockerfile"),
    read("backend/.dockerignore"),
    read("backend/pyproject.toml"),
  ]);

  assert.match(
    dockerfile,
    /python:3\.12\.13-slim-bookworm@sha256:[a-f0-9]{64}/u,
  );
  assert.match(dockerfile, /ghcr\.io\/astral-sh\/uv:0\.11\.28@sha256:[a-f0-9]{64}/u);
  assert.match(
    dockerfile,
    /uv sync --locked --no-dev --no-group executor --no-editable/u,
  );
  assert.doesNotMatch(dockerfile, /latest|playwright install|apt-get.*(?:chrome|chromium)/u);
  assert.doesNotMatch(
    pyproject,
    /dependencies\s*=\s*\[[\s\S]*?"playwright==1\.61\.0"[\s\S]*?\]\s*\n\s*\[project\.scripts\]/u,
  );
  assert.match(
    pyproject,
    /executor\s*=\s*\[[^\]]*"playwright==1\.61\.0"[^\]]*\]/u,
  );
  assert.match(dockerfile, /USER 65532:65532/u);
  assert.match(dockerfile, /EXPOSE 8000/u);
  assert.match(dockerfile, /ENTRYPOINT \["automation-tool-control-plane-container"\]/u);
  assert.match(
    pyproject,
    /automation-tool-control-plane-container = "automation_tool\.control_plane\.bootstrap\.container_cli:main"/u,
  );
  for (const excluded of ["tests", ".venv", "__pycache__", ".pytest_cache", ".coverage"]) {
    assert.ok(dockerignore.split("\n").includes(excluded), `${excluded} must be excluded`);
  }
});

test("C10-02 fixes health shutdown and OCI identity at the image boundary", async () => {
  const [dockerfile, acceptance] = await Promise.all([
    read("backend/Dockerfile"),
    read("scripts/run_c10_02_acceptance.py"),
  ]);

  assert.match(dockerfile, /HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3/u);
  assert.match(dockerfile, /\/api\/v1\/health/u);
  assert.match(dockerfile, /STOPSIGNAL SIGTERM/u);
  assert.match(dockerfile, /org\.opencontainers\.image\.version/u);
  assert.match(dockerfile, /org\.opencontainers\.image\.revision/u);
  assert.match(dockerfile, /org\.opencontainers\.image\.source/u);
  assert.match(acceptance, /--read-only/u);
  assert.match(acceptance, /no-new-privileges/u);
  assert.match(acceptance, /cap-drop/u);
  assert.match(acceptance, /\/api\/v1\/version/u);
  assert.match(acceptance, /docker.*stop/u);
});
