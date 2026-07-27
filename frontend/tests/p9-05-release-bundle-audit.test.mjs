import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import { auditReleaseBundle } from "../scripts/audit-release-bundle.mjs";

const CATALOG_ROOT = "Contents/Resources/motion-catalog";

/**
 * Write the frozen catalog tree the release now installs, with `files` naming
 * exactly what the audit should tolerate.
 */
async function writeCatalog(bundle, files) {
  const root = join(bundle, CATALOG_ROOT);
  await mkdir(root, { recursive: true });
  await writeFile(
    join(root, "manifest.json"),
    JSON.stringify({ schemaVersion: 1, files: files.map((path) => ({ path })) }),
  );
  for (const path of files) {
    const target = join(root, path);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, "asset");
  }
  return root;
}


async function createBundle(platform = "macos") {
  const root = await mkdtemp(join(tmpdir(), "automation-tool-p905-bundle-"));
  const bundle = join(root, platform === "macos" ? "Automation Tool.app" : "installed");
  const executor =
    platform === "macos"
      ? join(bundle, "Contents/Resources/local-executor/package")
      : join(bundle, "local-executor/package");
  await mkdir(executor, { recursive: true });
  await writeFile(
    join(executor, platform === "macos" ? "automation-tool-executor" : "automation-tool-executor.exe"),
    "executor",
  );
  await writeFile(join(executor, "executor-manifest.v1.json"), "{}\n");
  await writeFile(join(executor, "executor-manifest.v1.sig"), "atems1.fixture\n");
  const desktop = join(bundle, platform === "macos" ? "Contents/MacOS/desktop" : "desktop.exe");
  await mkdir(dirname(desktop), { recursive: true });
  await writeFile(desktop, "desktop");
  return { bundle, executor, root };
}

test("P9-05 accepts only the fixed macOS and Windows release bundle layouts", async () => {
  for (const platform of ["macos", "windows"]) {
    const fixture = await createBundle(platform);
    try {
      const result = await auditReleaseBundle({
        bundleRoot: fixture.bundle,
        executorPackagePath: fixture.executor,
        platform,
      });
      assert.equal(result.fileCount, 4);
      assert.ok(result.packageSize > 0);
    } finally {
      await rm(fixture.root, { recursive: true, force: true });
    }
  }
});

test("P9-05 rejects runtime data, user material, credentials, and test/debug content", async () => {
  const cases = [
    ["browser-profiles/douyin/profile-marker", "private-profile"],
    ["local-executor/state/executor-ledger.sqlite3", "sqlite"],
    ["artifacts/diagnostics/trace.json", "trace"],
    ["logs/executor.log", "log"],
    ["materials/user-video.mp4", "video"],
    ["credentials/release.pfx", "certificate"],
    ["assets/driver.bin", "TAURI_WEBDRIVER_PORT"],
    ["assets/debug.txt", "http://127.0.0.1:1420"],
    ["assets/session.txt", "atds1.private-control-plane-session"],
    ["assets/private.pem", "-----BEGIN PRIVATE KEY-----"],
  ];
  for (const [relativePath, content] of cases) {
    const fixture = await createBundle();
    try {
      const target = join(fixture.bundle, relativePath);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, content);
      await assert.rejects(
        auditReleaseBundle({
          bundleRoot: fixture.bundle,
          executorPackagePath: fixture.executor,
          platform: "macos",
        }),
        /Release bundle is rejected/,
        relativePath,
      );
    } finally {
      await rm(fixture.root, { recursive: true, force: true });
    }
  }
});

test("P9-05 detects a forbidden marker split across streaming chunks", async () => {
  const fixture = await createBundle();
  try {
    const marker = Buffer.from("TAURI_WEBDRIVER_PORT");
    const content = Buffer.concat([Buffer.alloc(1024 * 1024 - 5, 0x61), marker]);
    await writeFile(join(fixture.bundle, "split-marker.bin"), content);
    await assert.rejects(
      auditReleaseBundle({
        bundleRoot: fixture.bundle,
        executorPackagePath: fixture.executor,
        platform: "macos",
      }),
      /Release bundle is rejected/,
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("P9-05 rejects misplaced Executors, links, and incomplete package trust metadata", async () => {
  const misplaced = await createBundle();
  try {
    await assert.rejects(
      auditReleaseBundle({
        bundleRoot: misplaced.bundle,
        executorPackagePath: join(misplaced.bundle, "executor"),
        platform: "macos",
      }),
      /Release bundle is rejected/,
    );
  } finally {
    await rm(misplaced.root, { recursive: true, force: true });
  }

  // PyInstaller trees legitimately carry relative symlinks that stay inside the
  // package: the material video Worker lays 53 dynamic libraries out that way
  // because that is where its loader looks. Rejecting every symlink outside the
  // browser was free while the executor was the only other payload — it has
  // none — and it is not free now. The property worth protecting is that a link
  // cannot reach outside the package, which the escaping case below still
  // covers. This mirrors the same narrowing already applied to the Python gate
  // in `scripts/check_embedded_browser_package.py`.
  const inside = await createBundle();
  try {
    await mkdir(join(inside.executor, "vendor"), { recursive: true });
    await writeFile(join(inside.executor, "vendor/libexample.dylib"), "payload");
    await symlink(
      "vendor/libexample.dylib",
      join(inside.executor, "libexample.dylib"),
    );
    await auditReleaseBundle({
      bundleRoot: inside.bundle,
      executorPackagePath: inside.executor,
      platform: "macos",
    });
  } finally {
    await rm(inside.root, { recursive: true, force: true });
  }

  const linked = await createBundle();
  try {
    await symlink(
      linked.executor,
      join(linked.bundle, "linked"),
      process.platform === "win32" ? "junction" : "dir",
    );
    await assert.rejects(
      auditReleaseBundle({
        bundleRoot: linked.bundle,
        executorPackagePath: linked.executor,
        platform: "macos",
      }),
      /Release bundle is rejected/,
    );
  } finally {
    await rm(linked.root, { recursive: true, force: true });
  }

  const incomplete = await createBundle();
  try {
    await rm(join(incomplete.executor, "executor-manifest.v1.sig"));
    await assert.rejects(
      auditReleaseBundle({
        bundleRoot: incomplete.bundle,
        executorPackagePath: incomplete.executor,
        platform: "macos",
      }),
      /Release bundle is rejected/,
    );
  } finally {
    await rm(incomplete.root, { recursive: true, force: true });
  }
});

test("P9-05 is wired into both platform candidates and one dispatch command", async () => {
  const repositoryRoot = new URL("../../", import.meta.url);
  const [packageSource, macRunner, windowsRunner, auditRunner, workflow] = await Promise.all([
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("scripts/run_p9_03_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL("scripts/run_p9_04_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL("scripts/run_p9_05_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL(".github/workflows/desktop.yml", repositoryRoot), "utf8"),
  ]);
  const packageDocument = JSON.parse(packageSource);
  assert.equal(
    packageDocument.scripts["test:p9-05-package-audit"],
    "uv run --project ../backend --locked python ../scripts/run_p9_05_acceptance.py",
  );
  assert.match(macRunner, /audit-release-bundle\.mjs/u);
  assert.match(macRunner, /["']--platform["'],\s*["']macos["']/u);
  assert.match(windowsRunner, /audit-release-bundle\.mjs/u);
  assert.match(windowsRunner, /["']--platform["'],\s*["']windows["']/u);
  assert.match(auditRunner, /run_p9_03_acceptance\.py/u);
  assert.match(auditRunner, /run_p9_04_acceptance\.py/u);
  assert.match(workflow, /run_p9_05_acceptance\.py/u);
});

/**
 * PC-16: four upstream parts ship a `.wav` sound effect, and `.wav` is on the
 * media suffix ban that keeps captured user content out of a package.
 *
 * The ban stays. What changes is that inside the frozen catalog a media file is
 * allowed exactly when the catalog's own manifest accounts for it — every file
 * in that tree carries a SHA-256 there and the aggregate is locked in
 * `motion-catalog-release.v1.json`. A blanket exemption for the directory would
 * be the shape this project distrusts: a list you can get past by putting your
 * file in the right folder.
 */
test("P9-05 accepts a catalog media asset its own manifest accounts for", async () => {
  const fixture = await createBundle();
  try {
    await writeCatalog(fixture.bundle, ["items/vpn-youtube-spot/assets/soft-pulse.wav"]);
    const result = await auditReleaseBundle({
      bundleRoot: fixture.bundle,
      executorPackagePath: fixture.executor,
      platform: "macos",
    });
    assert.ok(result.fileCount > 4);
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("P9-05 still refuses a catalog media file the manifest never named", async () => {
  const fixture = await createBundle();
  try {
    await writeCatalog(fixture.bundle, ["items/vpn-youtube-spot/assets/soft-pulse.wav"]);
    const smuggled = join(fixture.bundle, CATALOG_ROOT, "items/vpn-youtube-spot/assets/recording.wav");
    await writeFile(smuggled, "not in the manifest");
    await assert.rejects(
      auditReleaseBundle({
        bundleRoot: fixture.bundle,
        executorPackagePath: fixture.executor,
        platform: "macos",
      }),
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("P9-05 keeps refusing media outside the catalog even with a catalog present", async () => {
  const fixture = await createBundle();
  try {
    await writeCatalog(fixture.bundle, ["items/vpn-youtube-spot/assets/soft-pulse.wav"]);
    const target = join(fixture.bundle, "Contents/Resources/materials/user-video.mp4");
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, "video");
    await assert.rejects(
      auditReleaseBundle({
        bundleRoot: fixture.bundle,
        executorPackagePath: fixture.executor,
        platform: "macos",
      }),
    );
  } finally {
    await rm(fixture.root, { recursive: true, force: true });
  }
});
