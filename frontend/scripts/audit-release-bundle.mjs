import { execFile } from "node:child_process";
import { createReadStream } from "node:fs";
import { lstat, readdir, readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const repositoryRoot = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const embeddedBrowserGate = resolve(repositoryRoot, "scripts/check_embedded_browser_package.py");
// A standard python.org install on Windows provides only `python`/`py`, so a
// POSIX-only candidate list would make the gate unreachable there — and an
// unreachable gate is a rejected bundle, not a skipped check.
const pythonCandidates = ["python3.12", "python3", "python"];

const maximumFiles = 20_000;
const maximumBytes = 16 * 1024 * 1024 * 1024;
const scanChunkSize = 1024 * 1024;
const developmentVerifyingKey = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg";
const forbiddenSegments = new Set([
  ".git",
  ".pytest_cache",
  "__pycache__",
  "artifacts",
  "browser-profiles",
  "credentials",
  "diagnostics",
  "downloads",
  "fixtures",
  "logs",
  "materials",
  "ms-playwright",
  "node_modules",
  "playwright-report",
  "screenshots",
  "test-results",
  "tests",
  "traces",
  "uploads",
  "user-content",
]);
const forbiddenNames = new Set([
  ".coverage",
  "cookies",
  "cookies-journal",
  "credentials.json",
  "executor-ledger.sqlite3",
  "history",
  "history-journal",
  "login data",
  "profile-marker",
  "secrets.json",
  "web data",
]);
const forbiddenSuffixes = [
  ".avi",
  ".db-journal",
  ".key",
  ".log",
  ".m4a",
  ".mkv",
  ".mobileprovision",
  ".mov",
  ".mp3",
  ".mp4",
  ".p12",
  ".pfx",
  ".sqlite",
  ".sqlite3",
  ".wav",
  ".webm",
];
const forbiddenContentMarkers = [
  "automation-tool-test-harness",
  "automation-tool-test-harness-adapter",
  "tauri-plugin-wdio",
  "tauri_plugin_wdio",
  "wdio-webdriver",
  "wdioTauri",
  "plugin:wdio|",
  "TAURI_WEBDRIVER_PORT",
  "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN",
  "AUTOMATION_TOOL_UPDATE_INSTALL_PROBE",
  "atds1.private-control-plane-session",
  "e4-14-hidden-app",
  "http://127.0.0.1:1420",
  "ws://127.0.0.1:1420",
  "http://localhost:1420",
  "ws://localhost:1420",
  developmentVerifyingKey,
].map((marker) => Buffer.from(marker));
// FFmpeg and other crypto-capable binaries legitimately embed the names of
// supported PEM formats as null-terminated strings. A private key header is a
// line, so require its line ending instead of rejecting a compiled capability
// string that contains no key material.
for (const header of [
  "-----BEGIN PRIVATE KEY-----",
  "-----BEGIN RSA PRIVATE KEY-----",
  "-----BEGIN EC PRIVATE KEY-----",
  "-----BEGIN OPENSSH PRIVATE KEY-----",
]) {
  forbiddenContentMarkers.push(Buffer.from(`${header}\n`));
  forbiddenContentMarkers.push(Buffer.from(`${header}\r\n`));
}
forbiddenContentMarkers.push(Buffer.from(developmentVerifyingKey, "base64url"));
const maximumMarkerLength = Math.max(...forbiddenContentMarkers.map((marker) => marker.length));

function rejected() {
  return new Error("Release bundle is rejected");
}

async function requireContainedSymlink(link, root, state) {
  let target;
  try {
    target = await realpath(link);
  } catch {
    // Missing target or a link loop: it cannot be shown to stay inside, and at
    // runtime it is an unexplained failure rather than a missing file.
    throw rejected();
  }
  const packageRoot = await realpath(root);
  const rendered = relative(packageRoot, target).replaceAll("\\", "/");
  if (isAbsolute(rendered) || rendered.startsWith("../")) {
    throw rejected();
  }
  // Only file links are legitimate. A directory link gives one tree two paths,
  // which would let a payload sit somewhere the "executor lives here" checks
  // never look. PyInstaller only ever links individual libraries.
  if (!(await lstat(target)).isFile()) {
    throw rejected();
  }
  if (
    state.embeddedBrowser !== undefined &&
    (rendered === state.embeddedBrowser ||
      rendered.startsWith(`${state.embeddedBrowser}/`))
  ) {
    throw rejected();
  }
}

function normalizedRelative(root, path) {
  const rendered = relative(root, path).replaceAll("\\", "/");
  if (rendered === "" || rendered === "." || isAbsolute(rendered) || rendered.startsWith("../")) {
    throw rejected();
  }
  return rendered;
}

/**
 * The frozen catalog ships four upstream sound effects, and `.wav` is on the
 * media ban that keeps captured user content out of a package.
 *
 * The ban is not lifted for the directory — that would be a list you get past
 * by putting your file in the right folder. It is lifted for a *file the
 * catalog's own manifest accounts for*: every file in that tree carries a
 * SHA-256 there, and the aggregate of all of them is locked in
 * `motion-catalog-release.v1.json`. So a media file is allowed exactly when
 * something already had to declare it.
 */
function accountedForByCatalog(rendered, state) {
  if (state.catalogFiles === undefined || state.catalogRoot === undefined) {
    return false;
  }
  if (!rendered.startsWith(`${state.catalogRoot}/`)) {
    return false;
  }
  return state.catalogFiles.has(rendered.slice(state.catalogRoot.length + 1));
}

/**
 * What the frozen catalog says it contains, if this package carries one.
 *
 * Absent catalog means an empty answer rather than a rejection: this audit runs
 * on Windows installs and on fixtures that have no catalog, and a missing
 * resource is `assertPackagedReleaseResources`' job to report, with a message
 * that names it.
 */
async function readCatalogManifest(root, platform) {
  const catalogRoot =
    platform === "macos" ? "Contents/Resources/motion-catalog" : "motion-catalog";
  try {
    const raw = await readFile(resolve(root, catalogRoot, "manifest.json"), "utf8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed.files)) {
      throw rejected();
    }
    return {
      catalogRoot,
      catalogFiles: new Set(parsed.files.map((file) => String(file.path))),
    };
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") {
      return { catalogRoot: undefined, catalogFiles: undefined };
    }
    throw rejected();
  }
}

function assertSafePath(rendered, state) {
  const segments = rendered.split("/");
  for (const segment of segments) {
    const lowered = segment.toLowerCase();
    if (
      lowered === ".env" ||
      lowered.startsWith(".env.") ||
      forbiddenSegments.has(lowered) ||
      forbiddenNames.has(lowered)
    ) {
      throw rejected();
    }
    if (
      forbiddenSuffixes.some((suffix) => lowered.endsWith(suffix)) &&
      !accountedForByCatalog(rendered, state)
    ) {
      throw rejected();
    }
  }
}

async function assertSafeContent(path) {
  let tail = Buffer.alloc(0);
  for await (const chunk of createReadStream(path, { highWaterMark: scanChunkSize })) {
    const combined = Buffer.concat([tail, chunk]);
    if (forbiddenContentMarkers.some((marker) => combined.indexOf(marker) !== -1)) {
      throw rejected();
    }
    tail = combined.subarray(Math.max(0, combined.length - maximumMarkerLength + 1));
  }
}

async function collectBundleFiles(directory, root, state) {
  const entries = await readdir(directory, { withFileTypes: true });
  entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    const rendered = normalizedRelative(root, path);
    assertSafePath(rendered, state);
    if (rendered === state.embeddedBrowser) {
      continue;
    }
    const metadata = await lstat(path);
    if (entry.isSymbolicLink() || metadata.isSymbolicLink()) {
      // A link that stays inside the package is legitimate: PyInstaller lays
      // the material video Worker's 53 dynamic libraries out this way and its
      // loader will not start without them. What must never happen is a link
      // reaching outside the package, into the digest-verified browser tree, or
      // pointing at nothing. Same narrowing as the Python gate in
      // `scripts/check_embedded_browser_package.py`.
      await requireContainedSymlink(path, root, state);
      continue;
    }
    if (metadata.isDirectory()) {
      await collectBundleFiles(path, root, state);
      continue;
    }
    if (!metadata.isFile()) {
      throw rejected();
    }
    state.fileCount += 1;
    state.packageSize += metadata.size;
    if (state.fileCount > maximumFiles || state.packageSize > maximumBytes) {
      throw rejected();
    }
    await assertSafeContent(path);
    state.files.add(rendered);
  }
}

async function declaredDistributionTarget(browser) {
  const manifest = JSON.parse(
    await readFile(resolve(browser, "distribution-manifest.v1.json"), "utf8"),
  );
  const target = manifest?.target;
  if (typeof target !== "string" || !/^[a-z0-9][a-z0-9_-]{0,31}$/.test(target)) {
    throw rejected();
  }
  return target;
}

async function requireEmbeddedBrowserDigestGate(root, browser, platform) {
  const target = await declaredDistributionTarget(browser);
  const parameters = [
    embeddedBrowserGate,
    "--bundle-root",
    root,
    "--target",
    target,
    "--platform",
    platform,
  ];
  let lastFailure;
  for (const interpreter of pythonCandidates) {
    try {
      await execFileAsync(interpreter, parameters, { maxBuffer: 16 * 1024 * 1024 });
      return;
    } catch (error) {
      if (error?.code === "ENOENT") {
        lastFailure = error;
        continue;
      }
      // Fixed rejection for the caller; the gate's own fixed message keeps a
      // rejection diagnosable. Only the first stdout line is relayed — that is
      // where the gate prints its fixed `release package rejected: …` text.
      // Its stderr carries Python tracebacks on unexpected failures, which
      // would leak repository and bundle absolute paths, so it is dropped.
      const [firstLine = ""] = String(error?.stdout ?? "").split("\n");
      process.stderr.write(`${firstLine}\n`);
      throw rejected();
    }
  }
  process.stderr.write(`embedded browser digest gate interpreter unavailable: ${lastFailure}\n`);
  throw rejected();
}

export async function auditReleaseBundle({
  bundleRoot,
  embeddedBrowserPath,
  executorPackagePath,
  platform,
}) {
  try {
    if (platform !== "macos" && platform !== "windows") {
      throw rejected();
    }
    const root = resolve(bundleRoot);
    const executor = resolve(executorPackagePath);
    const expectedExecutor =
      platform === "macos"
        ? "Contents/Resources/local-executor/package"
        : "local-executor/package";
    if (normalizedRelative(root, executor) !== expectedExecutor) {
      throw rejected();
    }
    const [rootMetadata, executorMetadata] = await Promise.all([lstat(root), lstat(executor)]);
    if (
      !rootMetadata.isDirectory() ||
      rootMetadata.isSymbolicLink() ||
      !executorMetadata.isDirectory() ||
      executorMetadata.isSymbolicLink()
    ) {
      throw rejected();
    }

    // The embedded Chromium distribution is the one subtree this scanner
    // cannot own: it is upstream code with declared symlinks. Skipping it is
    // therefore bound to the stronger digest gate — this scanner itself runs
    // `scripts/check_embedded_browser_package.py` over the same bundle.
    //
    // The binding is on the resource EXISTING, never on the caller declaring
    // it. A caller-controlled switch would gate nothing where it matters: a
    // bundler that dereferences the tree, and every Windows target, ship no
    // symlink at all, so the unexcluded scan would accept a tampered or
    // incomplete browser with exit code 0. `--embedded-browser` may only
    // confirm the one legal location.
    const expectedBrowser =
      platform === "macos" ? "Contents/Resources/embedded-browser" : "embedded-browser";
    const browser = resolve(root, expectedBrowser);
    if (
      embeddedBrowserPath !== undefined &&
      normalizedRelative(root, resolve(embeddedBrowserPath)) !== expectedBrowser
    ) {
      throw rejected();
    }
    let browserMetadata;
    try {
      browserMetadata = await lstat(browser);
    } catch (error) {
      // Only a genuinely absent resource means "this bundle ships no browser";
      // anything else (permission, I/O) must not degrade into skipping it.
      if (error?.code !== "ENOENT") {
        throw rejected();
      }
    }
    let embeddedBrowser;
    if (browserMetadata !== undefined) {
      if (!browserMetadata.isDirectory() || browserMetadata.isSymbolicLink()) {
        throw rejected();
      }
      await requireEmbeddedBrowserDigestGate(root, browser, platform);
      embeddedBrowser = expectedBrowser;
    } else if (embeddedBrowserPath !== undefined) {
      throw rejected();
    }

    const { catalogRoot, catalogFiles } = await readCatalogManifest(root, platform);
    const state = {
      catalogFiles,
      catalogRoot,
      embeddedBrowser,
      fileCount: 0,
      files: new Set(),
      packageSize: 0,
    };
    await collectBundleFiles(root, root, state);
    const entry = `${expectedExecutor}/${
      platform === "macos" ? "automation-tool-executor" : "automation-tool-executor.exe"
    }`;
    for (const required of [
      entry,
      `${expectedExecutor}/executor-manifest.v1.json`,
      `${expectedExecutor}/executor-manifest.v1.sig`,
    ]) {
      if (!state.files.has(required)) {
        throw rejected();
      }
    }
    return Object.freeze({ fileCount: state.fileCount, packageSize: state.packageSize });
  } catch (error) {
    if (error instanceof Error && error.message === "Release bundle is rejected") {
      throw error;
    }
    throw rejected();
  }
}

function parseArguments(arguments_) {
  const values = new Map();
  for (let index = 0; index < arguments_.length; index += 2) {
    const key = arguments_[index];
    const value = arguments_[index + 1];
    if (!key?.startsWith("--") || value === undefined || values.has(key)) {
      throw rejected();
    }
    values.set(key, value);
  }
  const required = ["--bundle-root", "--executor-package", "--platform"];
  const optional = ["--embedded-browser"];
  if (
    required.some((key) => !values.has(key)) ||
    [...values.keys()].some((key) => !required.includes(key) && !optional.includes(key))
  ) {
    throw rejected();
  }
  return values;
}

async function runCommand() {
  const arguments_ = parseArguments(process.argv.slice(2));
  const result = await auditReleaseBundle({
    bundleRoot: arguments_.get("--bundle-root"),
    embeddedBrowserPath: arguments_.get("--embedded-browser"),
    executorPackagePath: arguments_.get("--executor-package"),
    platform: arguments_.get("--platform"),
  });
  process.stdout.write(
    `[P9-05] Release bundle audit passed: ${result.fileCount} files, ${result.packageSize} bytes\n`,
  );
}

const isCommand = process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isCommand) {
  await runCommand();
}
