import { createReadStream } from "node:fs";
import { lstat, readdir } from "node:fs/promises";
import { isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

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
  "-----BEGIN PRIVATE KEY-----",
  "-----BEGIN RSA PRIVATE KEY-----",
  "-----BEGIN EC PRIVATE KEY-----",
  "-----BEGIN OPENSSH PRIVATE KEY-----",
  developmentVerifyingKey,
].map((marker) => Buffer.from(marker));
forbiddenContentMarkers.push(Buffer.from(developmentVerifyingKey, "base64url"));
const maximumMarkerLength = Math.max(...forbiddenContentMarkers.map((marker) => marker.length));

function rejected() {
  return new Error("Release bundle is rejected");
}

function normalizedRelative(root, path) {
  const rendered = relative(root, path).replaceAll("\\", "/");
  if (rendered === "" || rendered === "." || isAbsolute(rendered) || rendered.startsWith("../")) {
    throw rejected();
  }
  return rendered;
}

function assertSafePath(rendered) {
  const segments = rendered.split("/");
  for (const segment of segments) {
    const lowered = segment.toLowerCase();
    if (
      lowered === ".env" ||
      lowered.startsWith(".env.") ||
      forbiddenSegments.has(lowered) ||
      forbiddenNames.has(lowered) ||
      forbiddenSuffixes.some((suffix) => lowered.endsWith(suffix))
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
    assertSafePath(rendered);
    const metadata = await lstat(path);
    if (entry.isSymbolicLink() || metadata.isSymbolicLink()) {
      throw rejected();
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

export async function auditReleaseBundle({ bundleRoot, executorPackagePath, platform }) {
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

    const state = { fileCount: 0, files: new Set(), packageSize: 0 };
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
  if (
    values.size !== 3 ||
    !values.has("--bundle-root") ||
    !values.has("--executor-package") ||
    !values.has("--platform")
  ) {
    throw rejected();
  }
  return values;
}

async function runCommand() {
  const arguments_ = parseArguments(process.argv.slice(2));
  const result = await auditReleaseBundle({
    bundleRoot: arguments_.get("--bundle-root"),
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
