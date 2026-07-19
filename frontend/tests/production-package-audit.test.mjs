import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { auditProductionPackage } from "../scripts/audit-production-package.mjs";

const ACCEPTANCE_VERIFYING_KEY = "GX9rI-FshTLGq8g4-s1ep4m-DHaykgM0A5v6iz02jWE";
const DEVELOPMENT_VERIFYING_KEY = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg";

test("E4-15 release builds validate the Executor trust root before Tauri packaging", async () => {
  const [buildScript, cargoManifest] = await Promise.all([
    readFile(new URL("../src-tauri/build.rs", import.meta.url), "utf8"),
    readFile(new URL("../src-tauri/Cargo.toml", import.meta.url), "utf8"),
  ]);
  assert.match(buildScript, /cargo:rerun-if-env-changed=AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY/);
  assert.match(buildScript, /PROFILE/);
  assert.match(buildScript, /URL_SAFE_NO_PAD/);
  assert.match(buildScript, /VerifyingKey::from_bytes/);
  assert.match(buildScript, /is_weak/);
  assert.match(buildScript, /release Executor verification key is required/);
  assert.match(buildScript, /release Executor verification key is invalid/);
  assert.match(cargoManifest, /\[build-dependencies\][\s\S]*base64/);
  assert.match(cargoManifest, /\[build-dependencies\][\s\S]*ed25519-dalek/);
});

test("E4-15 formal runner builds an ephemeral release artifact and audits it in desktop CI", async () => {
  const [packageJson, runner, workflow] = await Promise.all([
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../../scripts/run_e4_15_acceptance.py", import.meta.url), "utf8"),
    readFile(new URL("../../.github/workflows/desktop.yml", import.meta.url), "utf8"),
  ]);
  assert.match(packageJson, /audit:production-package/);
  assert.match(packageJson, /test:e4-15/);
  assert.match(runner, /TemporaryDirectory\(prefix="automation-tool-e415-target-"/);
  assert.match(runner, /cargo["'],\s*["']check/);
  assert.match(runner, /--release/);
  assert.match(runner, /release Executor verification key is required/);
  assert.match(runner, /release Executor verification key is invalid/);
  assert.match(runner, /WEAK_VERIFYING_KEY/);
  assert.match(runner, /pnpm["'],\s*["']tauri["'],\s*["']build["'],\s*["']--no-bundle/);
  assert.match(runner, /audit-production-package\.mjs/);
  assert.match(workflow, /run_e4_15_acceptance\.py/);
  assert.doesNotMatch(runner, /tauri dev|open\(|Popen/);
});

async function createProductionFixture() {
  const root = await mkdtemp(join(tmpdir(), "automation-tool-package-audit-"));
  const distribution = join(root, "dist");
  const assets = join(distribution, "assets");
  const binary = join(root, process.platform === "win32" ? "desktop.exe" : "desktop");
  const tauriConfig = join(root, "tauri.conf.json");
  const cargoManifest = join(root, "Cargo.toml");

  await mkdir(assets, { recursive: true });
  await writeFile(join(distribution, "index.html"), "<main>desktop</main>");
  await writeFile(join(assets, "app.js"), "console.log('production desktop')");
  await writeFile(
    binary,
    Buffer.concat([Buffer.alloc(4096, 0x7f), Buffer.from(ACCEPTANCE_VERIFYING_KEY)]),
  );
  await writeFile(
    tauriConfig,
    JSON.stringify({
      identifier: "com.aventador.automationtool",
      app: {
        withGlobalTauri: false,
        windows: [{ label: "main", title: "desktop" }],
        security: {
          capabilities: ["main"],
          csp: { "default-src": "'self'", "connect-src": "ipc: http://ipc.localhost" },
        },
      },
      bundle: { active: true },
    }),
  );
  await writeFile(
    cargoManifest,
    `[features]\ndesktop-test-driver = ["dep:tauri-plugin-wdio", "dep:tauri-plugin-wdio-webdriver"]\n\n[dependencies]\ntauri = "2"\ntauri-plugin-wdio = { version = "1", optional = true }\ntauri-plugin-wdio-webdriver = { version = "1", optional = true }\n`,
  );
  return { binary, cargoManifest, distribution, root, tauriConfig };
}

test("E4-15 accepts only a release artifact with an isolated production dependency tree", async () => {
  const fixture = await createProductionFixture();
  try {
    await assert.doesNotReject(
      auditProductionPackage({
        binaryPath: fixture.binary,
        cargoManifestPath: fixture.cargoManifest,
        dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
        distributionPath: fixture.distribution,
        expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
        tauriConfigPath: fixture.tauriConfig,
      }),
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("E4-15 rejects test commands, drivers, sidecars, origins, and debug ports in the binary", async () => {
  const forbidden = [
    "tauri-plugin-wdio",
    "TAURI_WEBDRIVER_PORT",
    "prepare_executor_lifecycle_for_acceptance",
    "inject_executor_crash_for_acceptance",
    "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN",
    "com.aventador.automationtool.e414acceptance",
    "e4-14-hidden-app",
    "http://127.0.0.1:1420",
  ];
  for (const marker of forbidden) {
    const fixture = await createProductionFixture();
    try {
      await writeFile(
        fixture.binary,
        Buffer.concat([Buffer.from(ACCEPTANCE_VERIFYING_KEY), Buffer.from(marker)]),
      );
      await assert.rejects(
        auditProductionPackage({
          binaryPath: fixture.binary,
          cargoManifestPath: fixture.cargoManifest,
          dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
          distributionPath: fixture.distribution,
          expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
          tauriConfigPath: fixture.tauriConfig,
        }),
        /Production package contains a forbidden test or debug marker/,
        marker,
      );
    } finally {
      await rm(fixture.root, { recursive: true, force: true });
    }
  }
});

test("E4-15 rejects a test dependency, test resource, or non-production Tauri capability", async () => {
  const cases = [
    {
      mutate: async () => ({
        dependencyTree: "automation-tool-desktop v0.1.0\ntauri-plugin-wdio v1.2.0",
      }),
      pattern: /Production dependency tree contains a desktop test dependency/,
    },
    {
      mutate: async (fixture) => {
        const config = JSON.parse(await readFile(fixture.tauriConfig, "utf8"));
        config.bundle.resources = ["bin/fake-test-sidecar"];
        await writeFile(fixture.tauriConfig, JSON.stringify(config));
        return {};
      },
      pattern: /Production Tauri config contains a test resource or sidecar/,
    },
    {
      mutate: async (fixture) => {
        const config = JSON.parse(await readFile(fixture.tauriConfig, "utf8"));
        config.app.withGlobalTauri = true;
        await writeFile(fixture.tauriConfig, JSON.stringify(config));
        return {};
      },
      pattern: /Production Tauri config is not least privilege/,
    },
  ];

  for (const item of cases) {
    const fixture = await createProductionFixture();
    try {
      const overrides = await item.mutate(fixture);
      await assert.rejects(
        auditProductionPackage({
          binaryPath: fixture.binary,
          cargoManifestPath: fixture.cargoManifest,
          dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
          distributionPath: fixture.distribution,
          expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
          tauriConfigPath: fixture.tauriConfig,
          ...overrides,
        }),
        item.pattern,
      );
    } finally {
      await rm(fixture.root, { recursive: true, force: true });
    }
  }
});

test("E4-15 rejects missing, malformed, absent, or development Executor trust roots", async () => {
  const fixture = await createProductionFixture();
  try {
    for (const expectedVerifyingKey of [
      undefined,
      "not-base64url",
      DEVELOPMENT_VERIFYING_KEY,
    ]) {
      await assert.rejects(
        auditProductionPackage({
          binaryPath: fixture.binary,
          cargoManifestPath: fixture.cargoManifest,
          dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
          distributionPath: fixture.distribution,
          expectedVerifyingKey,
          tauriConfigPath: fixture.tauriConfig,
        }),
        /Release Executor verification key is unavailable or invalid/,
      );
    }

    await writeFile(fixture.binary, Buffer.alloc(4096, 0x7f));
    await assert.rejects(
      auditProductionPackage({
        binaryPath: fixture.binary,
        cargoManifestPath: fixture.cargoManifest,
        dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
        distributionPath: fixture.distribution,
        expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
        tauriConfigPath: fixture.tauriConfig,
      }),
      /Production binary does not contain the expected release verification key/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});
