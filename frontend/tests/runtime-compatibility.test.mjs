import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("P9-08 keeps one exact App Executor Control Plane release matrix", async () => {
  const [matrixSource, backendVersions, rustVersions, cargoSource, pythonSource] =
    await Promise.all([
      readFile(
        new URL("contracts/protocol/runtime-compatibility-v1.json", repositoryRoot),
        "utf8",
      ),
      readFile(
        new URL("backend/src/automation_tool/protocol/version.py", repositoryRoot),
        "utf8",
      ),
      readFile(
        new URL("frontend/src-tauri/src/runtime_compatibility.rs", repositoryRoot),
        "utf8",
      ),
      readFile(new URL("frontend/src-tauri/Cargo.toml", repositoryRoot), "utf8"),
      readFile(new URL("backend/pyproject.toml", repositoryRoot), "utf8"),
    ]);
  const matrix = JSON.parse(matrixSource);

  assert.deepEqual(matrix, {
    version: "runtime.compatibility.v1",
    release: "0.1.0",
    desktopApp: {
      version: "0.1.0",
      controlPlane: "=0.1.0",
      controlPlaneApi: "v1",
      executorRuntime: "=0.1.0",
      executorProtocol: "1.0",
    },
    controlPlane: {
      version: "0.1.0",
      desktopApp: { minimum: "0.1.0", maximum: "0.1.0" },
      executorRuntime: { minimum: "0.1.0", maximum: "0.1.0" },
      executorProtocol: { minimum: "1.0", maximum: "1.0" },
    },
    failurePolicy: [
      "malformed_version_rejected",
      "older_version_rejected",
      "newer_version_rejected",
      "prerelease_version_rejected",
      "api_mismatch_rejected",
      "protocol_mismatch_rejected",
    ],
  });

  assert.match(backendVersions, /EXECUTOR_RUNTIME_VERSION[^\n]*"0\.1\.0"/u);
  assert.match(backendVersions, /EXECUTOR_PROTOCOL[^\n]*"1\.0"/u);
  assert.match(backendVersions, /API_VERSION[^\n]*"v1"/u);
  assert.match(rustVersions, /CONTROL_PLANE_VERSION[^\n]*"0\.1\.0"/u);
  assert.match(rustVersions, /EXECUTOR_RUNTIME_VERSION[^\n]*"0\.1\.0"/u);
  assert.match(rustVersions, /EXECUTOR_PROTOCOL[^\n]*"1\.0"/u);
  assert.match(rustVersions, /CONTROL_PLANE_API_VERSION[^\n]*"v1"/u);
  assert.match(cargoSource, /^version = "0\.1\.0"$/mu);
  assert.match(pythonSource, /^version = "0\.1\.0"$/mu);
});

test("P9-08 binds wrong versions to production startup and Hello rejection", async () => {
  const [desktopClient, executorPlatform, executorConnections] = await Promise.all([
    readFile(
      new URL("frontend/src-tauri/src/control_plane.rs", repositoryRoot),
      "utf8",
    ),
    readFile(
      new URL("frontend/src-tauri/src/executor_platform.rs", repositoryRoot),
      "utf8",
    ),
    readFile(
      new URL(
        "backend/src/automation_tool/control_plane/application/executor_connections.py",
        repositoryRoot,
      ),
      "utf8",
    ),
  ]);

  assert.match(desktopClient, /ControlPlaneOperation::GetSystemVersion/u);
  assert.match(desktopClient, /parse_system_version_response\(&version_body/u);
  assert.match(executorPlatform, /EXECUTOR_RUNTIME_VERSION_REQUIREMENT/u);
  assert.match(
    executorConnections,
    /payload\.executor_version != CURRENT_EXECUTOR_RUNTIME_VERSION/u,
  );
});
