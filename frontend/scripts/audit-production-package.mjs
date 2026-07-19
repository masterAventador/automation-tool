import { execFile } from "node:child_process";
import { readdir, readFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { assertProductionBoundaries } from "./check-production-boundaries.mjs";

const execFileAsync = promisify(execFile);
const frontendRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const developmentVerifyingKey = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg";
const forbiddenBinaryMarkers = [
  "tauri-plugin-wdio",
  "tauri_plugin_wdio",
  "wdio-webdriver",
  "wdioTauri",
  "plugin:wdio|",
  "TAURI_WEBDRIVER_PORT",
  "run_control_plane_acceptance",
  "register_installation_for_revocation_acceptance",
  "create_task_for_acceptance",
  "query_tasks_for_acceptance",
  "stream_task_events_for_acceptance",
  "prepare_task_projection_for_acceptance",
  "prepare_task_create_form_for_acceptance",
  "prepare_task_run_for_acceptance",
  "prepare_task_lifecycle_for_acceptance",
  "prepare_executor_lifecycle_for_acceptance",
  "prepare_task_restart_for_acceptance",
  "prepare_workbench_for_acceptance",
  "control_task_for_acceptance",
  "terminate_tasks_for_acceptance",
  "inject_executor_crash_for_acceptance",
  "inject_executor_hang_for_acceptance",
  "exit_app_for_acceptance",
  "AUTOMATION_TOOL_CONTROL_PLANE_E2E_ORIGIN",
  "AUTOMATION_TOOL_E414_BOOTSTRAP_TOKEN",
  "com.aventador.automationtool.e414acceptance",
  "e4-14-hidden-app",
  "automation-tool-test-harness",
  "http://127.0.0.1:1420",
  "ws://127.0.0.1:1420",
  "http://localhost:1420",
  "ws://localhost:1420",
];
const forbiddenResourceMarkers = [
  "acceptance",
  "desktop-e2e",
  "control-plane-e2e",
  "fake-executor",
  "fake_executor",
  "test-sidecar",
  "test_sidecar",
  "webdriver",
  "wdio",
];

function containsBytes(haystack, needle) {
  return haystack.indexOf(needle) !== -1;
}

function decodeExpectedVerifyingKey(source) {
  if (typeof source !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(source)) {
    throw new Error("Release Executor verification key is unavailable or invalid");
  }
  const decoded = Buffer.from(source, "base64url");
  if (decoded.length !== 32 || decoded.toString("base64url") !== source) {
    throw new Error("Release Executor verification key is unavailable or invalid");
  }
  if (source === developmentVerifyingKey) {
    throw new Error("Release Executor verification key is unavailable or invalid");
  }
  return decoded;
}

function assertLeastPrivilegeTauriConfig(configuration) {
  const security = configuration?.app?.security;
  const windows = configuration?.app?.windows;
  const capabilities = security?.capabilities;
  const productionCsp = JSON.stringify(security?.csp ?? {});
  if (
    configuration?.identifier !== "com.aventador.automationtool" ||
    configuration?.bundle?.active !== true ||
    configuration?.app?.withGlobalTauri !== false ||
    !Array.isArray(windows) ||
    windows.length !== 1 ||
    windows[0]?.label !== "main" ||
    windows[0]?.visible === false ||
    !Array.isArray(capabilities) ||
    capabilities.length !== 1 ||
    capabilities[0] !== "main" ||
    /127\.0\.0\.1:1420|localhost:1420|webdriver|wdio/i.test(productionCsp)
  ) {
    throw new Error("Production Tauri config is not least privilege");
  }

  const bundledPaths = [
    ...stringsIn(configuration?.bundle?.resources),
    ...stringsIn(configuration?.bundle?.externalBin),
  ];
  if (
    bundledPaths.some((path) => {
      const normalized = path.toLowerCase();
      return forbiddenResourceMarkers.some((marker) => normalized.includes(marker));
    })
  ) {
    throw new Error("Production Tauri config contains a test resource or sidecar");
  }
}

function stringsIn(value) {
  if (typeof value === "string") {
    return [value];
  }
  if (Array.isArray(value)) {
    return value.flatMap(stringsIn);
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value).flatMap(([key, nested]) => [key, ...stringsIn(nested)]);
  }
  return [];
}

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = resolve(directory, entry.name);
      return entry.isDirectory() ? filesUnder(path) : [path];
    }),
  );
  return nested.flat();
}

async function assertNoTestAssets(distributionPath) {
  await assertProductionBoundaries(distributionPath);
  for (const path of await filesUnder(distributionPath)) {
    const normalized = relative(distributionPath, path).toLowerCase();
    if (forbiddenResourceMarkers.some((marker) => normalized.includes(marker))) {
      throw new Error("Production assets contain a test resource");
    }
  }
}

function assertProductionDependencyTree(dependencyTree) {
  if (/tauri-plugin-wdio|tauri_plugin_wdio|wdio-webdriver|webdriver/i.test(dependencyTree)) {
    throw new Error("Production dependency tree contains a desktop test dependency");
  }
}

function assertTestDependenciesRemainOptional(cargoManifest) {
  for (const dependency of ["tauri-plugin-wdio", "tauri-plugin-wdio-webdriver"]) {
    const declaration = new RegExp(
      `^${dependency}\\s*=\\s*\\{[^\\n]*optional\\s*=\\s*true[^\\n]*\\}$`,
      "mu",
    );
    if (!declaration.test(cargoManifest)) {
      throw new Error("Desktop test dependencies are not optional in Cargo.toml");
    }
  }
  if (/^default\s*=\s*\[[^\]]*(desktop-test-driver|desktop-e2e|control-plane-e2e)/mu.test(cargoManifest)) {
    throw new Error("A desktop test feature is enabled by default");
  }
}

export async function auditProductionPackage({
  binaryPath,
  cargoManifestPath,
  dependencyTree,
  distributionPath,
  expectedVerifyingKey,
  tauriConfigPath,
}) {
  const decodedExpectedKey = decodeExpectedVerifyingKey(expectedVerifyingKey);
  const [binary, cargoManifest, tauriConfigSource] = await Promise.all([
    readFile(binaryPath),
    readFile(cargoManifestPath, "utf8"),
    readFile(tauriConfigPath, "utf8"),
  ]);
  const tauriConfig = JSON.parse(tauriConfigSource);

  assertProductionDependencyTree(dependencyTree);
  assertTestDependenciesRemainOptional(cargoManifest);
  assertLeastPrivilegeTauriConfig(tauriConfig);
  await assertNoTestAssets(distributionPath);

  for (const marker of forbiddenBinaryMarkers) {
    if (containsBytes(binary, Buffer.from(marker))) {
      throw new Error(`Production package contains a forbidden test or debug marker: ${marker}`);
    }
  }

  const developmentKeyBytes = Buffer.from(developmentVerifyingKey, "base64url");
  if (
    containsBytes(binary, Buffer.from(developmentVerifyingKey)) ||
    containsBytes(binary, developmentKeyBytes)
  ) {
    throw new Error("Production package contains the development Executor verification key");
  }
  if (
    !containsBytes(binary, Buffer.from(expectedVerifyingKey)) &&
    !containsBytes(binary, decodedExpectedKey)
  ) {
    throw new Error("Production binary does not contain the expected release verification key");
  }
}

function parseArguments(arguments_) {
  const values = new Map();
  for (let index = 0; index < arguments_.length; index += 2) {
    const key = arguments_[index];
    const value = arguments_[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("Production package audit arguments are invalid");
    }
    values.set(key, value);
  }
  return values;
}

async function runCommand() {
  const arguments_ = parseArguments(process.argv.slice(2));
  const cargoManifestPath = resolve(
    arguments_.get("--cargo-manifest") ?? resolve(frontendRoot, "src-tauri/Cargo.toml"),
  );
  const binaryPath = arguments_.get("--binary");
  if (binaryPath === undefined) {
    throw new Error("Production binary path is required");
  }
  const { stdout: dependencyTree } = await execFileAsync(
    "cargo",
    [
      "tree",
      "--manifest-path",
      cargoManifestPath,
      "--locked",
      "--edges",
      "normal",
      "--no-default-features",
      "--prefix",
      "none",
    ],
    { cwd: frontendRoot, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 },
  );
  await auditProductionPackage({
    binaryPath: resolve(binaryPath),
    cargoManifestPath,
    dependencyTree,
    distributionPath: resolve(arguments_.get("--dist") ?? resolve(frontendRoot, "dist")),
    expectedVerifyingKey: process.env.AUTOMATION_TOOL_EXECUTOR_VERIFYING_KEY,
    tauriConfigPath: resolve(
      arguments_.get("--tauri-config") ?? resolve(frontendRoot, "src-tauri/tauri.conf.json"),
    ),
  });
  process.stdout.write("[E4-15] Production desktop package audit passed\n");
}

const isCommand = process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isCommand) {
  await runCommand();
}
