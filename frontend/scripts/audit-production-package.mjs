import { execFile } from "node:child_process";
import { lstat, readdir, readFile } from "node:fs/promises";
import { basename, dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { assertProductionBoundaries } from "./check-production-boundaries.mjs";

const execFileAsync = promisify(execFile);
const frontendRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const repositoryRoot = dirname(frontendRoot);
// One declaration of what a distributable package must carry, shared with
// `scripts/release_assembly.py` (which derives its video runtime installer from
// it) and `scripts/check_release_package_wiring.py`. Re-listing the resources
// here would recreate exactly the drift this audit exists to catch.
const releaseResourceContract = resolve(
  repositoryRoot,
  "contracts/quality/release-package-resources.v1.json",
);
const developmentVerifyingKey = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg";
const forbiddenBinaryMarkers = [
  "tauri-plugin-wdio",
  "tauri_plugin_wdio",
  "wdio-webdriver",
  "wdioTauri",
  "plugin:wdio|",
  "TAURI_WEBDRIVER_PORT",
  // Kept as a byte marker because it is a WebDriver host name that never
  // legitimately appears in shipped assets. Other driver names and hidden
  // test window configuration are checked structurally instead: unqualified
  // substring matching over a binary that embeds the whole frontend bundle
  // would fail the release on unrelated third-party JSON or locale data.
  "tauri-driver",
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
  "com.aventador.automationtool.b504acceptance",
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
// Capabilities a distributable package must be able to reach. These are Tauri
// command names, not UI copy, so they survive rewording and minification and
// they disappear precisely when the code that invokes them is compiled out.
//
// The product account login used to be mounted only when Vite built in
// `customer-demo` mode. Release packages are built in the default mode, so the
// account gateway was tree-shaken out and the shipped package had no way to log
// in to a Control Plane that requires one — while every gate stayed green,
// because every statement this audit made about package contents was a
// negative one. Whether a deployment *requires* a login is configuration;
// whether the package can present one is not.
// Exported so a fixture cannot claim to model a release package while omitting
// a capability the real audit demands: `scripts/test_embedded_browser_package.py`
// reads this list rather than restating it.
export const requiredDistributionMarkers = [
  "restore_product_account_session",
  "login_product_account",
];
const forbiddenRuntimeDataMarkers = [
  "browser-profiles",
  "cookies",
  "executor-ledger",
  "artifacts/diagnostics",
  ".sqlite",
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
      const normalized = path.replaceAll("\\", "/").toLowerCase();
      return forbiddenResourceMarkers.some((marker) => normalized.includes(marker));
    })
  ) {
    throw new Error("Production Tauri config contains a test resource or sidecar");
  }
  if (
    bundledPaths.some((path) => {
      const normalized = path.replaceAll("\\", "/").toLowerCase();
      return forbiddenRuntimeDataMarkers.some((marker) => normalized.includes(marker));
    })
  ) {
    throw new Error("Production Tauri config contains runtime data");
  }
}

async function loadReleaseResources() {
  const contract = JSON.parse(await readFile(releaseResourceContract, "utf8"));
  if (!Array.isArray(contract?.resources) || contract.resources.length === 0) {
    throw new Error("Release package resource contract is unavailable or empty");
  }
  return contract;
}

function installedDestination(resource) {
  return resource.installedParts.join("/");
}

// Tauri accepts either a list of sources (each copied to its own relative path)
// or a source-to-destination map. Only the destination side can be compared
// against where the production resolver reads, so both forms are reduced to it.
function declaredResourceDestinations(resources) {
  const declared = Array.isArray(resources)
    ? resources
    : resources !== null && typeof resources === "object"
      ? Object.values(resources)
      : [];
  return new Set(
    declared
      .filter((value) => typeof value === "string")
      .map((value) => value.replaceAll("\\", "/").replace(/^\.\//u, "").replace(/\/+$/u, "")),
  );
}

function assertDeclaredReleaseResources(configuration, platform, contract) {
  const declared = declaredResourceDestinations(configuration?.bundle?.resources);
  for (const resource of contract.resources) {
    if (resource.bundlerDeclared?.[platform] !== true) {
      continue;
    }
    if (!declared.has(installedDestination(resource))) {
      throw new Error(
        `Production Tauri config does not declare a required release resource: ${resource.name}`,
      );
    }
  }
}

function requiredFilesFor(resource, platform) {
  if (platform !== "windows") {
    return resource.requiredFiles;
  }
  const executables = new Set(resource.windowsExecutables ?? []);
  return resource.requiredFiles.map((name) => (executables.has(name) ? `${name}.exe` : name));
}

async function assertPackagedReleaseResources(packageRoot, platform, contract) {
  const root = resolve(packageRoot, ...(contract.resourceRoot?.[platform] ?? []));
  for (const resource of contract.resources) {
    const missing = () =>
      new Error(
        `Production package is missing a required release resource: ${resource.name}`,
      );
    const location = join(root, ...resource.installedParts);
    let directory;
    try {
      directory = await lstat(location);
    } catch {
      throw missing();
    }
    if (!directory.isDirectory() || directory.isSymbolicLink()) {
      throw missing();
    }
    const required = requiredFilesFor(resource, platform);
    if (required.length === 0) {
      // The resource carries its own stronger gate (`verifiedBy`), so the only
      // thing asserted here is the one property that gate cannot state on a
      // tree that never arrived: it is present and it is not an empty shell.
      if ((await readdir(location)).length === 0) {
        throw missing();
      }
      continue;
    }
    for (const name of required) {
      let payload;
      try {
        payload = await lstat(join(location, ...name.split("/")));
      } catch {
        throw missing();
      }
      // A directory that merely exists is what the production resolver trips
      // over: it finds the path and then fails to launch.
      if (!payload.isFile() || payload.size === 0) {
        throw missing();
      }
    }
  }
}

// A macOS package is recognised from the audited artifact itself, never from a
// caller-supplied switch: the binary of a bundled App always sits at
// `<Name>.app/Contents/MacOS/<binary>`, and a `--no-bundle` release binary never
// does. Windows has no such structural marker — its release payload is an
// ordinary directory — so that target passes `--package-root` explicitly and
// `scripts/check_release_package_wiring.py` is what refuses a release path that
// leaves it out.
function macOsBundleContaining(binaryPath) {
  const parts = resolve(binaryPath).split(sep);
  if (
    parts.length >= 4 &&
    parts.at(-2) === "MacOS" &&
    parts.at(-3) === "Contents" &&
    basename(parts.at(-4)).endsWith(".app")
  ) {
    return parts.slice(0, -3).join(sep);
  }
  return undefined;
}

function resolvePackageTarget({ binaryPath, packageRoot, packagePlatform }) {
  if (packageRoot !== undefined) {
    if (packagePlatform !== "macos" && packagePlatform !== "windows") {
      throw new Error("Production package platform must be macos or windows");
    }
    return { platform: packagePlatform, root: resolve(packageRoot) };
  }
  const bundle = macOsBundleContaining(binaryPath);
  return bundle === undefined ? undefined : { platform: "macos", root: bundle };
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

// Vite writes every content-hashed file of a build under `assets/`, and Tauri
// stores each embedded asset's key as a plain string just ahead of that asset's
// compressed payload. The keys are therefore recoverable from the artifact
// itself even though the contents are not, and two builds of different sources
// never agree on them.
const hashedAssetDirectory = "assets";
const hashedAssetPrefix = `/${hashedAssetDirectory}/`;

function countBytes(haystack, needle) {
  let count = 0;
  for (let at = haystack.indexOf(needle); at !== -1; at = haystack.indexOf(needle, at + 1)) {
    count += 1;
  }
  return count;
}

function distributionHashedAssetKeys(distributionPath, files) {
  const keys = new Set();
  for (const path of files) {
    const rendered = relative(distributionPath, path).replaceAll("\\", "/");
    if (rendered.startsWith(`${hashedAssetDirectory}/`)) {
      keys.add(`/${rendered}`);
    }
  }
  return keys;
}

// `frontend/dist` is a single directory shared by every build in the checkout,
// so a concurrent `pnpm build:tauri:*-test` can replace it while a release audit
// is still running. Without this check the audit reports on whichever build
// happens to be on disk: on 2026-07-26 that produced one false rejection of a
// clean package, and one pass that survived by a 13 second margin. Binding the
// distribution to the binary makes the verdict a property of the artifact —
// a distribution this binary did not embed is refused outright instead of
// being described.
function assertDistributionBelongsToBinary(binary, distributionPath, files) {
  const provided = distributionHashedAssetKeys(distributionPath, files);
  // Membership, never extraction. Each key sits immediately before its own
  // compressed payload, so a key read *out* of the binary can run into whatever
  // byte follows it and be wrong; asking whether a key the distribution already
  // names is present cannot be. The occurrence count then closes the other
  // direction: the binary must embed no asset this distribution fails to
  // account for.
  const unembedded = [...provided].filter((key) => !containsBytes(binary, Buffer.from(key)));
  const embeddedCount = countBytes(binary, Buffer.from(hashedAssetPrefix));
  if (provided.size === 0 || unembedded.length > 0 || embeddedCount !== provided.size) {
    throw new Error(
      "Production distribution does not belong to the audited binary: " +
        `${provided.size} hashed asset(s) offered, ${unembedded.length} of them not embedded, ` +
        `${embeddedCount} embedded by the binary`,
    );
  }
}

async function assertProductionCapabilities(files) {
  const sources = await Promise.all(
    files
      .filter((path) => [".js", ".html"].includes(extname(path)))
      .map((path) => readFile(path, "utf8")),
  );
  const bundle = sources.join("\n");
  for (const marker of requiredDistributionMarkers) {
    if (!bundle.includes(marker)) {
      throw new Error(
        `Production assets cannot reach a required capability: ${marker}`,
      );
    }
  }
}

async function assertNoTestAssets(distributionPath, files) {
  await assertProductionBoundaries(distributionPath);
  for (const path of files) {
    const normalized = relative(distributionPath, path).replaceAll("\\", "/").toLowerCase();
    if (forbiddenResourceMarkers.some((marker) => normalized.includes(marker))) {
      throw new Error("Production assets contain a test resource");
    }
    if (forbiddenRuntimeDataMarkers.some((marker) => normalized.includes(marker))) {
      throw new Error("Production assets contain runtime data");
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
  packagePlatform,
  packageRoot,
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

  // Until now everything this audit said about `bundle.resources` was a
  // negative: it looked for forbidden strings, so an empty declaration and a
  // complete one were indistinguishable and three video runtime resources
  // shipped absent with every gate green. A package is now also asked the
  // positive question — does it declare, and does it carry, each resource the
  // production Rust code resolves out of its resource directory.
  const target = resolvePackageTarget({ binaryPath, packagePlatform, packageRoot });
  if (target !== undefined) {
    const contract = await loadReleaseResources();
    assertDeclaredReleaseResources(tauriConfig, target.platform, contract);
    await assertPackagedReleaseResources(target.root, target.platform, contract);
  }

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

  // The binary is judged first and on its own bytes, so a genuinely bad
  // artifact keeps its precise diagnosis. Only once the binary is accepted does
  // the distribution have to prove it is that binary's own product, before any
  // statement is made about its contents.
  const files = await filesUnder(distributionPath);
  assertDistributionBelongsToBinary(binary, distributionPath, files);
  await assertProductionCapabilities(files);
  await assertNoTestAssets(distributionPath, files);
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
    packagePlatform: arguments_.get("--package-platform"),
    packageRoot: arguments_.get("--package-root"),
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
