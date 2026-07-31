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
const privateKeyHeaders = [
  "-----BEGIN PRIVATE KEY-----",
  "-----BEGIN RSA PRIVATE KEY-----",
  "-----BEGIN EC PRIVATE KEY-----",
  "-----BEGIN OPENSSH PRIVATE KEY-----",
].map((header) => Buffer.from(header));
const privateKeyLineWhitespace = new Set([0x09, 0x0b, 0x0c, 0x20]);
// FFmpeg and other crypto-capable binaries legitimately embed the names of
// supported PEM formats as null-terminated strings. A private key header is a
// line, so require optional OpenSSL-accepted horizontal whitespace plus a line
// ending instead of rejecting a compiled capability string with no key data.
forbiddenContentMarkers.push(Buffer.from(developmentVerifyingKey, "base64url"));
const maximumMarkerLength = Math.max(
  ...forbiddenContentMarkers.map((marker) => marker.length),
  ...privateKeyHeaders.map((header) => header.length),
);
const rejectionPrefix = "Release bundle is rejected";

class ReleaseBundleRejection extends Error {
  constructor(reason, rendered) {
    super(
      `${rejectionPrefix}: ${reason}${rendered === undefined ? "" : ` (${rendered})`}`,
    );
    this.name = "ReleaseBundleRejection";
  }
}

function rejected(reason, rendered) {
  return new ReleaseBundleRejection(reason, rendered);
}

async function requireContainedSymlink(link, root, state) {
  const linkRendered = normalizedRelative(root, link);
  let target;
  try {
    target = await realpath(link);
  } catch {
    // Missing target or a link loop: it cannot be shown to stay inside, and at
    // runtime it is an unexplained failure rather than a missing file.
    throw rejected("symlink target cannot be resolved", linkRendered);
  }
  const packageRoot = await realpath(root);
  const rendered = relative(packageRoot, target).replaceAll("\\", "/");
  if (isAbsolute(rendered) || rendered.startsWith("../")) {
    throw rejected("symlink target escapes the bundle", linkRendered);
  }
  // Only file links are legitimate. A directory link gives one tree two paths,
  // which would let a payload sit somewhere the "executor lives here" checks
  // never look. PyInstaller only ever links individual libraries.
  if (!(await lstat(target)).isFile()) {
    throw rejected("symlink target is not a regular file", linkRendered);
  }
  if (
    state.embeddedBrowser !== undefined &&
    (rendered === state.embeddedBrowser ||
      rendered.startsWith(`${state.embeddedBrowser}/`))
  ) {
    throw rejected("symlink targets the digest-gated browser tree", linkRendered);
  }
}

function normalizedRelative(root, path) {
  const rendered = relative(root, path).replaceAll("\\", "/");
  if (rendered === "" || rendered === "." || isAbsolute(rendered) || rendered.startsWith("../")) {
    throw rejected("path is outside the bundle boundary");
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
      throw rejected("catalog manifest has no files array", `${catalogRoot}/manifest.json`);
    }
    return {
      catalogRoot,
      catalogFiles: new Set(parsed.files.map((file) => String(file.path))),
    };
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") {
      return { catalogRoot: undefined, catalogFiles: undefined };
    }
    if (error instanceof ReleaseBundleRejection) {
      throw error;
    }
    throw rejected("catalog manifest is unreadable or invalid", `${catalogRoot}/manifest.json`);
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
      throw rejected("forbidden path or name", rendered);
    }
    if (
      forbiddenSuffixes.some((suffix) => lowered.endsWith(suffix)) &&
      !accountedForByCatalog(rendered, state)
    ) {
      throw rejected("unaccounted media or secret-bearing suffix", rendered);
    }
  }
}

async function assertSafeContent(path, rendered) {
  let tail = Buffer.alloc(0);
  let pendingPrivateKeyWhitespace = false;
  let pendingPrivateKeyCarriageReturn = false;
  for await (const chunk of createReadStream(path, { highWaterMark: scanChunkSize })) {
    let offset = 0;
    while (
      (pendingPrivateKeyWhitespace || pendingPrivateKeyCarriageReturn) &&
      offset < chunk.length
    ) {
      const byte = chunk[offset];
      if (
        (pendingPrivateKeyWhitespace && byte === 0x0a) ||
        (pendingPrivateKeyCarriageReturn && byte === 0x0a)
      ) {
        throw rejected("forbidden content marker", rendered);
      }
      const nextWhitespace =
        pendingPrivateKeyWhitespace && privateKeyLineWhitespace.has(byte);
      const nextCarriageReturn = pendingPrivateKeyWhitespace && byte === 0x0d;
      pendingPrivateKeyWhitespace = nextWhitespace;
      pendingPrivateKeyCarriageReturn = nextCarriageReturn;
      if (!nextWhitespace && !nextCarriageReturn) {
        break;
      }
      offset += 1;
    }
    if (
      (pendingPrivateKeyWhitespace || pendingPrivateKeyCarriageReturn) &&
      offset === chunk.length
    ) {
      tail = Buffer.alloc(0);
      continue;
    }
    const combined = Buffer.concat([tail, chunk.subarray(offset)]);
    if (forbiddenContentMarkers.some((marker) => combined.indexOf(marker) !== -1)) {
      throw rejected("forbidden content marker", rendered);
    }
    let nextPendingWhitespace = false;
    let nextPendingCarriageReturn = false;
    for (const header of privateKeyHeaders) {
      let index = combined.indexOf(header);
      while (index !== -1) {
        let cursor = index + header.length;
        while (
          cursor < combined.length &&
          privateKeyLineWhitespace.has(combined[cursor])
        ) {
          cursor += 1;
        }
        if (cursor === combined.length) {
          nextPendingWhitespace = true;
          break;
        }
        if (combined[cursor] === 0x0a) {
          throw rejected("forbidden content marker", rendered);
        }
        if (combined[cursor] === 0x0d) {
          if (cursor + 1 === combined.length) {
            nextPendingCarriageReturn = true;
            break;
          }
          if (combined[cursor + 1] === 0x0a) {
            throw rejected("forbidden content marker", rendered);
          }
        }
        index = combined.indexOf(header, index + 1);
      }
    }
    pendingPrivateKeyWhitespace = nextPendingWhitespace;
    pendingPrivateKeyCarriageReturn = nextPendingCarriageReturn;
    if (pendingPrivateKeyWhitespace || pendingPrivateKeyCarriageReturn) {
      tail = Buffer.alloc(0);
      continue;
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
      throw rejected("special file is forbidden", rendered);
    }
    state.fileCount += 1;
    state.packageSize += metadata.size;
    if (state.fileCount > maximumFiles) {
      throw rejected("file count exceeds the release limit", rendered);
    }
    if (state.packageSize > maximumBytes) {
      throw rejected("package bytes exceed the release limit", rendered);
    }
    await assertSafeContent(path, rendered);
    state.files.add(rendered);
  }
}

async function declaredDistributionTarget(browser, browserRendered) {
  const rendered = `${browserRendered}/distribution-manifest.v1.json`;
  let manifest;
  try {
    manifest = JSON.parse(
      await readFile(resolve(browser, "distribution-manifest.v1.json"), "utf8"),
    );
  } catch {
    throw rejected("embedded browser distribution manifest is unreadable or invalid", rendered);
  }
  const target = manifest?.target;
  if (typeof target !== "string" || !/^[a-z0-9][a-z0-9_-]{0,31}$/.test(target)) {
    throw rejected("embedded browser distribution target is invalid", rendered);
  }
  return target;
}

async function requireEmbeddedBrowserDigestGate(root, browser, platform) {
  const browserRendered = normalizedRelative(root, browser);
  const target = await declaredDistributionTarget(browser, browserRendered);
  const parameters = [
    embeddedBrowserGate,
    "--bundle-root",
    root,
    "--target",
    target,
    "--platform",
    platform,
  ];
  for (const interpreter of pythonCandidates) {
    try {
      await execFileAsync(interpreter, parameters, { maxBuffer: 16 * 1024 * 1024 });
      return;
    } catch (error) {
      if (error?.code === "ENOENT") {
        continue;
      }
      // Fixed rejection for the caller; the gate's own fixed message keeps a
      // rejection diagnosable. Only the first stdout line is relayed — that is
      // where the gate prints its fixed `release package rejected: …` text.
      // Its stderr carries Python tracebacks on unexpected failures, which
      // would leak repository and bundle absolute paths, so it is dropped.
      const [firstLine = ""] = String(error?.stdout ?? "").split("\n");
      process.stderr.write(`${firstLine}\n`);
      throw rejected("embedded browser digest gate failed", browserRendered);
    }
  }
  throw rejected("embedded browser digest gate interpreter is unavailable", browserRendered);
}

export async function auditReleaseBundle({
  bundleRoot,
  embeddedBrowserPath,
  executorPackagePath,
  platform,
}) {
  try {
    if (platform !== "macos" && platform !== "windows") {
      throw rejected("platform must be macos or windows");
    }
    const root = resolve(bundleRoot);
    const executor = resolve(executorPackagePath);
    const expectedExecutor =
      platform === "macos"
        ? "Contents/Resources/local-executor/package"
        : "local-executor/package";
    if (normalizedRelative(root, executor) !== expectedExecutor) {
      throw rejected("executor package is not at the required release path", expectedExecutor);
    }
    let rootMetadata;
    try {
      rootMetadata = await lstat(root);
    } catch {
      throw rejected("bundle root is missing or unreadable");
    }
    let executorMetadata;
    try {
      executorMetadata = await lstat(executor);
    } catch {
      throw rejected("executor package is missing or unreadable", expectedExecutor);
    }
    if (!rootMetadata.isDirectory() || rootMetadata.isSymbolicLink()) {
      throw rejected("bundle root is not a regular directory");
    }
    if (!executorMetadata.isDirectory() || executorMetadata.isSymbolicLink()) {
      throw rejected("executor package is not a regular directory", expectedExecutor);
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
      throw rejected(
        "declared embedded browser is not at the required release path",
        expectedBrowser,
      );
    }
    let browserMetadata;
    try {
      browserMetadata = await lstat(browser);
    } catch (error) {
      // Only a genuinely absent resource means "this bundle ships no browser";
      // anything else (permission, I/O) must not degrade into skipping it.
      if (error?.code !== "ENOENT") {
        throw rejected("embedded browser cannot be inspected", expectedBrowser);
      }
    }
    let embeddedBrowser;
    if (browserMetadata !== undefined) {
      if (!browserMetadata.isDirectory() || browserMetadata.isSymbolicLink()) {
        throw rejected("embedded browser is not a regular directory", expectedBrowser);
      }
      await requireEmbeddedBrowserDigestGate(root, browser, platform);
      embeddedBrowser = expectedBrowser;
    } else if (embeddedBrowserPath !== undefined) {
      throw rejected("declared embedded browser is missing", expectedBrowser);
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
        throw rejected("required release file is missing", required);
      }
    }
    return Object.freeze({ fileCount: state.fileCount, packageSize: state.packageSize });
  } catch (error) {
    if (error instanceof ReleaseBundleRejection) {
      throw error;
    }
    throw rejected("unexpected audit failure");
  }
}

function parseArguments(arguments_) {
  const values = new Map();
  for (let index = 0; index < arguments_.length; index += 2) {
    const key = arguments_[index];
    const value = arguments_[index + 1];
    if (!key?.startsWith("--") || value === undefined || values.has(key)) {
      throw rejected("command arguments must be unique --key value pairs");
    }
    values.set(key, value);
  }
  const required = ["--bundle-root", "--executor-package", "--platform"];
  const optional = ["--embedded-browser"];
  if (
    required.some((key) => !values.has(key)) ||
    [...values.keys()].some((key) => !required.includes(key) && !optional.includes(key))
  ) {
    throw rejected("required command arguments are missing or unknown");
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
  try {
    await runCommand();
  } catch (error) {
    const safe =
      error instanceof ReleaseBundleRejection
        ? error
        : rejected("unexpected command failure");
    process.stderr.write(`${safe.message}\n`);
    process.exitCode = 1;
  }
}
