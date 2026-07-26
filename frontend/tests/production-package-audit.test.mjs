import assert from "node:assert/strict";
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
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
  assert.match(runner, /TemporaryDirectory\(\s*prefix="automation-tool-e415-target-"/);
  assert.match(runner, /cargo["'],\s*["']check/);
  assert.match(runner, /--release/);
  assert.match(runner, /release Executor verification key is required/);
  assert.match(runner, /release Executor verification key is invalid/);
  assert.match(runner, /WEAK_VERIFYING_KEY/);
  assert.match(runner, /pnpm_executable\(\),\s*["']tauri["'],\s*["']build["'],\s*["']--no-bundle/);
  assert.match(runner, /"pnpm.cmd" if sys.platform == "win32" else "pnpm"/);
  assert.match(runner, /audit-production-package\.mjs/);
  assert.match(workflow, /run_e4_15_acceptance\.py/);
  assert.doesNotMatch(runner, /tauri dev|open\(|Popen/);
});

// A real Tauri binary stores every embedded asset's key as a plain string just
// ahead of that asset's compressed payload, so a fixture binary only models the
// artifact under audit if it carries the same keys.
function embeddedAssetPayload(keys) {
  return Buffer.concat(
    keys.flatMap((key) => [Buffer.from(key), Buffer.alloc(64, 0xa5)]),
  );
}

async function createProductionFixture({ assetName = "app.js" } = {}) {
  const root = await mkdtemp(join(tmpdir(), "automation-tool-package-audit-"));
  const distribution = join(root, "dist");
  const assets = join(distribution, "assets");
  const binary = join(root, process.platform === "win32" ? "desktop.exe" : "desktop");
  const tauriConfig = join(root, "tauri.conf.json");
  const cargoManifest = join(root, "Cargo.toml");

  await mkdir(assets, { recursive: true });
  await writeFile(join(distribution, "index.html"), "<main>desktop</main>");
  await writeFile(
    join(assets, assetName),
    "console.log('production desktop');invoke('restore_product_account_session');" +
      "invoke('login_product_account')",
  );
  await writeFile(
    binary,
    Buffer.concat([
      Buffer.alloc(4096, 0x7f),
      embeddedAssetPayload(["/index.html", `/assets/${assetName}`]),
      Buffer.from(ACCEPTANCE_VERIFYING_KEY),
    ]),
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

test("E4-15 refuses a package whose assets cannot reach the product account login", async () => {
  const fixture = await createProductionFixture();
  try {
    // Exactly what a build fork produces. The login screen used to be mounted
    // only when Vite compiled in `customer-demo` mode, so the release build
    // tree-shook the account gateway away and shipped a package that could not
    // log in to anything — and every gate stayed green. A package audit that
    // only looks for forbidden strings cannot see a missing capability, so it
    // has to be asked the positive question too.
    await writeFile(
      join(fixture.distribution, "assets", "app.js"),
      "console.log('production desktop')",
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
      /cannot reach a required capability: (restore_product_account_session|login_product_account)/u,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

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
    "com.aventador.automationtool.b504acceptance",
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

test("H8-15 rejects runtime Profile, database, Cookie, and diagnostic data", async () => {
  for (const relativePath of [
    "embedded-browser-profiles/douyin/profile-marker",
    "browser-profiles/douyin/legacy-profile-marker",
    "Cookies",
    "executor-ledger.sqlite3",
    "artifacts/diagnostics/trace.json",
  ]) {
    const fixture = await createProductionFixture();
    try {
      const target = join(fixture.distribution, relativePath);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, "private-runtime-data");
      await assert.rejects(
        auditProductionPackage({
          binaryPath: fixture.binary,
          cargoManifestPath: fixture.cargoManifest,
          dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
          distributionPath: fixture.distribution,
          expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
          tauriConfigPath: fixture.tauriConfig,
        }),
        /Production assets contain runtime data/,
      );
    } finally {
      await rm(fixture.root, { recursive: true, force: true });
    }
  }

  const fixture = await createProductionFixture();
  try {
    const config = JSON.parse(await readFile(fixture.tauriConfig, "utf8"));
    config.bundle.resources = ["executor-ledger.sqlite3"];
    await writeFile(fixture.tauriConfig, JSON.stringify(config));
    await assert.rejects(
      auditProductionPackage({
        binaryPath: fixture.binary,
        cargoManifestPath: fixture.cargoManifest,
        dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
        distributionPath: fixture.distribution,
        expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
        tauriConfigPath: fixture.tauriConfig,
      }),
      /Production Tauri config contains runtime data/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

// `frontend/dist` is one directory shared by every build in the checkout: a
// concurrent `pnpm build:tauri:*-test` rewrites it in `desktop-e2e` mode while a
// release audit is still running. Auditing it in place let a foreign build
// decide the verdict on this artifact — twice on 2026-07-26, once as a false
// rejection and once with a 13 second margin. The audit must therefore refuse
// any distribution it cannot show the audited binary embedded.
async function overwriteWithForeignTestBuild(distribution) {
  await rm(join(distribution, "assets"), { recursive: true, force: true });
  await mkdir(join(distribution, "assets"), { recursive: true });
  await writeFile(
    join(distribution, "assets", "index-FOREIGNtest.js"),
    "globalThis.wdioTauri = true;",
  );
  await writeFile(join(distribution, "harness.html"), "<main>harness</main>");
}

test("E4-15 refuses a distribution the audited binary never embedded", async () => {
  const fixture = await createProductionFixture({ assetName: "index-PRODUCTION.js" });
  try {
    await overwriteWithForeignTestBuild(fixture.distribution);
    await assert.rejects(
      auditProductionPackage({
        binaryPath: fixture.binary,
        cargoManifestPath: fixture.cargoManifest,
        dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
        distributionPath: fixture.distribution,
        expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
        tauriConfigPath: fixture.tauriConfig,
      }),
      /Production distribution does not belong to the audited binary/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("E4-15 refuses a distribution that drops an asset the audited binary embedded", async () => {
  const fixture = await createProductionFixture({ assetName: "index-PRODUCTION.js" });
  try {
    await rm(join(fixture.distribution, "assets", "index-PRODUCTION.js"));
    await assert.rejects(
      auditProductionPackage({
        binaryPath: fixture.binary,
        cargoManifestPath: fixture.cargoManifest,
        dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
        distributionPath: fixture.distribution,
        expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
        tauriConfigPath: fixture.tauriConfig,
      }),
      /Production distribution does not belong to the audited binary/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("E4-15 keeps its verdict on the audited artifact while the shared build directory is overwritten", async () => {
  const fixture = await createProductionFixture({ assetName: "index-PRODUCTION.js" });
  const snapshot = join(fixture.root, "audited-distribution");
  try {
    await cp(fixture.distribution, snapshot, { recursive: true });
    await overwriteWithForeignTestBuild(fixture.distribution);
    await assert.doesNotReject(
      auditProductionPackage({
        binaryPath: fixture.binary,
        cargoManifestPath: fixture.cargoManifest,
        dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
        distributionPath: snapshot,
        expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
        tauriConfigPath: fixture.tauriConfig,
      }),
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("every package audit runner freezes the distribution it audits instead of reading the shared one", async () => {
  const runners = [
    "run_e4_15_acceptance.py",
    "run_eb_16_acceptance.py",
    "run_eb_16_windows_acceptance.py",
    "run_p9_03_acceptance.py",
    "run_p9_04_acceptance.py",
  ];
  for (const runner of runners) {
    const source = await readFile(new URL(`../../scripts/${runner}`, import.meta.url), "utf8");
    assert.match(
      source,
      /snapshot_production_assets/u,
      `${runner} must audit a frozen copy of the distribution the build consumed`,
    );
    assert.doesNotMatch(
      source,
      /"--dist",\s*\n?\s*os\.fspath\(PRODUCTION_ASSETS\)/u,
      `${runner} must not hand the shared frontend/dist to the audit`,
    );
  }
});

// The five resource trees the production Rust code resolves out of the packaged
// resource directory. On 2026-07-26 three of them shipped absent: every gate
// stayed green because the only statement the audit made about `bundle.resources`
// was a negative one — it looked for forbidden strings, so an empty declaration
// and a complete one were indistinguishable to it. These fixtures build a real
// packaged layout so the audit can be asked the positive question instead.
const MACOS_PACKAGE_RESOURCES = {
  "embedded-browser/distribution-manifest.v1.json": '{"target":"macos-arm64"}',
  "local-executor/package/automation-tool-executor": "executor",
  "media-toolchain/bin/ffmpeg": "ffmpeg",
  "media-toolchain/bin/ffprobe": "ffprobe",
  "media-toolchain/manifest.json": "{}",
  "motion-video-worker/package/runtime/node": "node",
  "motion-video-worker/package/app/worker.mjs": "export {};",
  "material-video-worker/package/automation-tool-material-video-worker": "worker",
};

async function createPackagedFixture({ omit = [], declare = ["local-executor/package/"] } = {}) {
  const fixture = await createProductionFixture({ assetName: "index-PRODUCTION.js" });
  const packageRoot = join(fixture.root, "自动化运营工具.app");
  const binary = join(packageRoot, "Contents", "MacOS", "自动化运营工具");
  await mkdir(dirname(binary), { recursive: true });
  await cp(fixture.binary, binary);
  for (const [relativePath, payload] of Object.entries(MACOS_PACKAGE_RESOURCES)) {
    if (omit.some((prefix) => relativePath.startsWith(prefix))) {
      continue;
    }
    const target = join(packageRoot, "Contents", "Resources", relativePath);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, payload);
  }
  const config = JSON.parse(await readFile(fixture.tauriConfig, "utf8"));
  config.bundle.resources = Object.fromEntries(
    declare.map((destination) => [`build/${destination}`, destination]),
  );
  await writeFile(fixture.tauriConfig, JSON.stringify(config));
  return { ...fixture, binary, packageRoot };
}

function auditPackaged(fixture, overrides = {}) {
  return auditProductionPackage({
    binaryPath: fixture.binary,
    cargoManifestPath: fixture.cargoManifest,
    dependencyTree: "automation-tool-desktop v0.1.0\ntauri v2.11.5",
    distributionPath: fixture.distribution,
    expectedVerifyingKey: ACCEPTANCE_VERIFYING_KEY,
    tauriConfigPath: fixture.tauriConfig,
    ...overrides,
  });
}

test("P9-05 accepts a package that carries every declared release resource", async () => {
  const fixture = await createPackagedFixture();
  try {
    await assert.doesNotReject(auditPackaged(fixture));
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("P9-05 refuses a production config that declares no bundler resource at all", async () => {
  const fixture = await createPackagedFixture({ declare: [] });
  try {
    await assert.rejects(
      auditPackaged(fixture),
      /Production Tauri config does not declare a required release resource: local-executor/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("P9-05 refuses a package whose video runtime never reached the resource directory", async () => {
  for (const resource of ["media-toolchain", "motion-video-worker", "material-video-worker"]) {
    const fixture = await createPackagedFixture({ omit: [resource] });
    try {
      await assert.rejects(
        auditPackaged(fixture),
        new RegExp(`Production package is missing a required release resource: ${resource}`),
        resource,
      );
    } finally {
      await rm(fixture.root, { recursive: true, force: true });
    }
  }
});

test("P9-05 refuses a package whose browser or Executor tree is absent", async () => {
  for (const resource of ["embedded-browser", "local-executor"]) {
    const fixture = await createPackagedFixture({ omit: [resource] });
    try {
      await assert.rejects(
        auditPackaged(fixture),
        new RegExp(`Production package is missing a required release resource: ${resource}`),
        resource,
      );
    } finally {
      await rm(fixture.root, { recursive: true, force: true });
    }
  }
});

test("P9-05 refuses a package whose required resource file is present but empty", async () => {
  const fixture = await createPackagedFixture();
  try {
    await writeFile(
      join(fixture.packageRoot, "Contents/Resources/motion-video-worker/package/runtime/node"),
      "",
    );
    await assert.rejects(
      auditPackaged(fixture),
      /Production package is missing a required release resource: motion-video-worker/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("P9-05 requires every release resource to be declared on Windows, where the bundler ships them all", async () => {
  const fixture = await createPackagedFixture();
  try {
    await assert.rejects(
      auditPackaged(fixture, {
        packageRoot: join(fixture.packageRoot, "Contents/Resources"),
        packagePlatform: "windows",
      }),
      /Production Tauri config does not declare a required release resource: embedded-browser/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
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
