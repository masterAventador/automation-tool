#!/usr/bin/env node
/** Isolated process boundary for the motion-composition runtime. */

import { execFile, spawn, spawnSync } from "node:child_process";
import { createHmac, timingSafeEqual } from "node:crypto";
import { access, lstat, mkdir, mkdtemp, open, realpath, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { extname, isAbsolute, join, sep } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";

const WORKER_VERSION = "0.7.68";
const PROTOCOL_VERSION = "1.0";
const BOOTSTRAP_VERSION = "1";
const MAX_LINE_BYTES = 16 * 1024;
const EVENT_DOMAIN = "automation-tool.video-worker-event.v1\0";
const COMMAND_DOMAIN = "automation-tool.video-worker-command.v1\0";
const TOKEN_PATTERN = /^[0-9a-f]{64}$/;
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const FIXED_BOOTSTRAP_ERROR = "Motion composition worker bootstrap is rejected";
const RENDER_JOB_PREFIX = "automation-tool-renderjob-";
const CHROMIUM_MAJOR_MINIMUM = 100;
const CHROMIUM_MAJOR_MAXIMUM = 999;
const RENDER_TIMEOUT_SECONDS_MINIMUM = 1;
const RENDER_TIMEOUT_SECONDS_MAXIMUM = 60;
const CHROMIUM_VERSION_PATTERN = /(?:^|\s|\/)(\d+)\.\d+\.\d+\.\d+/;
// The fixed composition viewport every captured frame must match exactly.
// The created CDP target does not inherit `--window-size`, so the render
// session also forces these metrics through the DevTools protocol.
const RENDER_VIEWPORT_WIDTH = 640;
const RENDER_VIEWPORT_HEIGHT = 360;
const MAX_PROTOCOL_RESPONSE_BYTES = 64 * 1024;
const SANDBOX_FRAMES_MAXIMUM = 600;
const SANDBOX_SECONDS_MAXIMUM = 300;
const SANDBOX_MEMORY_MEGABYTES_MINIMUM = 128;
const SANDBOX_MEMORY_MEGABYTES_MAXIMUM = 8192;
const SANDBOX_OUTPUT_BYTES_MAXIMUM = 2147483647;
const SANDBOX_ASSETS_MAXIMUM = 128;
const SANDBOX_RELATIVE_PATH_MAXIMUM = 512;
const SANDBOX_MESSAGE_LIMIT_BYTES = 32 * 1024 * 1024;
const SANDBOX_FRAMES_DIRECTORY = "frames";
const SANDBOX_CANCEL_FILE = ".automation-tool-cancel";
// Two animation frames: the current style has actually been composited, so
// what is captured is what the seek asked for. Cheap enough to run per frame.
const COMPOSITED_EXPRESSION_BODY = `
  await new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(() => resolve(true))));
  return true;
`;
// The warm-up additionally decodes every image once, paid before the first
// kept frame rather than per frame. The bounded race matters: this runtime is
// disconnected by design, so an `<img>` pointing at a remote host never
// settles — `decode()` stays pending forever and `catch` never runs, which
// eats the whole render budget instead of costing one decode.
const WARM_UP_DECODE_BUDGET_MS = 250;
// How many settle-and-probe rounds the warm-up may take before the page
// is declared unable to reach a stable state.
const WARM_UP_STABLE_ATTEMPTS = 8;
const WARM_UP_EXPRESSION_BODY = `
  await Promise.race([
    Promise.all(Array.from(document.images)
      .map((image) => image.decode().catch(() => undefined))),
    new Promise((resolve) => setTimeout(resolve, ${WARM_UP_DECODE_BUDGET_MS})),
  ]);
  ${COMPOSITED_EXPRESSION_BODY}
`;
const RESOURCE_MONITOR_INTERVAL_MS = 300;
const SANDBOX_FAILURES = {
  cancelled: "render_cancelled",
  mismatch: "chromium_major_mismatch",
  output: "render_output_exceeded",
  protocol: "render_protocol_invalid",
  resource: "render_resource_exceeded",
  timeout: "render_timeout",
  unusable: "render_browser_unusable",
};

function fixedJson(value) {
  return JSON.stringify(value);
}

/** Deterministic JSON with recursively sorted keys; the sandbox HMAC binds to it. */
function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const fields = Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`);
    return `{${fields.join(",")}}`;
  }
  return JSON.stringify(value);
}

function killBrowserProcessGroup(child) {
  if (process.platform === "win32") {
    const systemRoot = process.env.SystemRoot ?? process.env.WINDIR;
    if (typeof systemRoot === "string" && systemRoot.length > 0) {
      spawnSync(
        join(systemRoot, "System32", "taskkill.exe"),
        ["/PID", String(child.pid), "/T", "/F"],
        { stdio: "ignore", windowsHide: true },
      );
    }
    try {
      child.kill("SIGKILL");
    } catch {
      // The direct child already exited.
    }
    return;
  }
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch {
    // The browser process group already exited.
  }
  try {
    child.kill("SIGKILL");
  } catch {
    // The direct child already exited.
  }
}

function hasExactKeys(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

async function executableFileMetadata(path) {
  if (typeof path !== "string" || !isAbsolute(path)) return null;
  let metadata;
  try {
    metadata = await lstat(path);
  } catch {
    return null;
  }
  if (!metadata.isFile() || metadata.isSymbolicLink()) return null;
  if (process.platform !== "win32") {
    if (!(metadata.mode & 0o111)) return null;
    return metadata;
  }
  if (extname(path).toLowerCase() !== ".exe") return null;
  let handle;
  try {
    handle = await open(path, "r");
    const signature = Buffer.alloc(2);
    const { bytesRead } = await handle.read(signature, 0, signature.length, 0);
    if (bytesRead !== 2 || !signature.equals(Buffer.from("MZ"))) return null;
  } catch {
    return null;
  } finally {
    await handle?.close();
  }
  return metadata;
}

function renderProcessEnvironment(jobDirectory) {
  const environment = { HOME: jobDirectory, TMPDIR: jobDirectory };
  if (process.platform !== "win32") return environment;
  environment.USERPROFILE = jobDirectory;
  environment.TEMP = jobDirectory;
  environment.TMP = jobDirectory;
  for (const name of ["SystemRoot", "WINDIR"]) {
    if (typeof process.env[name] === "string" && process.env[name].length > 0) {
      environment[name] = process.env[name];
    }
  }
  return environment;
}

async function validRenderBrowser(value) {
  if (value === null) return true;
  if (!hasExactKeys(value, ["chromiumMajor", "executablePath", "launchTimeoutSeconds"])) {
    return false;
  }
  if (!Number.isInteger(value.chromiumMajor)
      || value.chromiumMajor < CHROMIUM_MAJOR_MINIMUM
      || value.chromiumMajor > CHROMIUM_MAJOR_MAXIMUM) return false;
  if (!Number.isInteger(value.launchTimeoutSeconds)
      || value.launchTimeoutSeconds < RENDER_TIMEOUT_SECONDS_MINIMUM
      || value.launchTimeoutSeconds > RENDER_TIMEOUT_SECONDS_MAXIMUM) return false;
  return (await executableFileMetadata(value.executablePath)) !== null;
}

async function parseBootstrap(line) {
  if (Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) throw new Error("rejected");
  let value;
  try {
    value = JSON.parse(line);
  } catch {
    throw new Error("rejected");
  }
  if (!hasExactKeys(value, [
    "assetRoot", "bootstrapVersion", "enableWebUi", "localSessionToken",
    "protocolVersion", "renderBrowser", "scriptModel", "workerKind",
  ])) throw new Error("rejected");
  if (value.bootstrapVersion !== BOOTSTRAP_VERSION
      || value.protocolVersion !== PROTOCOL_VERSION
      || value.workerKind !== "node"
      || value.enableWebUi !== false
      || value.scriptModel !== null
      || typeof value.assetRoot !== "string"
      || !isAbsolute(value.assetRoot)
      || typeof value.localSessionToken !== "string"
      || !TOKEN_PATTERN.test(value.localSessionToken)
      || !(await validRenderBrowser(value.renderBrowser))) throw new Error("rejected");
  const metadata = await lstat(value.assetRoot);
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error("rejected");
  value.assetRoot = await realpath(value.assetRoot);
  return value;
}

function proof(token, domain, parts, prefix) {
  const mac = createHmac("sha256", Buffer.from(token, "hex"));
  mac.update(domain);
  mac.update(parts.join("\0"));
  return prefix + mac.digest("base64url");
}

function eventProof(bootstrap, event, detail) {
  return proof(
    bootstrap.localSessionToken,
    EVENT_DOMAIN,
    [event, "node", PROTOCOL_VERSION, WORKER_VERSION, detail],
    "atvwp1.",
  );
}

function validCommand(bootstrap, command, name) {
  if (!hasExactKeys(command, [
    "authenticationProof", "command", "jobId", "protocolVersion", "workerKind",
  ])) return false;
  if (command.command !== name
      || command.protocolVersion !== PROTOCOL_VERSION
      || command.workerKind !== "node"
      || typeof command.jobId !== "string"
      || !UUID_V4_PATTERN.test(command.jobId)) return false;
  const expected = proof(
    bootstrap.localSessionToken,
    COMMAND_DOMAIN,
    [name, "node", PROTOCOL_VERSION, command.jobId],
    "atvwc1.",
  );
  if (typeof command.authenticationProof !== "string") return false;
  const actualBytes = Buffer.from(command.authenticationProof);
  const expectedBytes = Buffer.from(expected);
  return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes);
}

function validSandboxRelativePath(value) {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > SANDBOX_RELATIVE_PATH_MAXIMUM
    || value.includes("\0")
    || value.includes("\\")
    || isAbsolute(value)
  ) return false;
  return value.split("/").every(
    (segment) => segment !== "" && segment !== "." && segment !== "..",
  );
}

function boundedInteger(value, minimum, maximum) {
  return Number.isInteger(value) && value >= minimum && value <= maximum;
}

function validSandboxSpec(value) {
  if (!hasExactKeys(value, [
    "allowedAssets", "entryHtml", "frameCount", "maxCpuSeconds",
    "maxDurationSeconds", "maxMemoryMegabytes", "maxOutputBytes", "workspace",
  ])) return false;
  if (typeof value.workspace !== "string" || !isAbsolute(value.workspace)) return false;
  if (!validSandboxRelativePath(value.entryHtml)) return false;
  if (
    !Array.isArray(value.allowedAssets)
    || value.allowedAssets.length > SANDBOX_ASSETS_MAXIMUM
    || !value.allowedAssets.every(validSandboxRelativePath)
  ) return false;
  return boundedInteger(value.frameCount, 1, SANDBOX_FRAMES_MAXIMUM)
    && boundedInteger(value.maxDurationSeconds, 1, SANDBOX_SECONDS_MAXIMUM)
    && boundedInteger(value.maxCpuSeconds, 1, SANDBOX_SECONDS_MAXIMUM)
    && boundedInteger(
      value.maxMemoryMegabytes,
      SANDBOX_MEMORY_MEGABYTES_MINIMUM,
      SANDBOX_MEMORY_MEGABYTES_MAXIMUM,
    )
    && boundedInteger(value.maxOutputBytes, 1, SANDBOX_OUTPUT_BYTES_MAXIMUM);
}

/**
 * Resolve the RenderJob workspace boundary: the entry document and every
 * declared asset must be a regular, non-symlink file whose real path stays
 * inside the workspace; the frames output directory must not exist yet.
 */
async function resolveSandboxWorkspace(spec) {
  let workspaceMetadata;
  try {
    workspaceMetadata = await lstat(spec.workspace);
  } catch {
    return null;
  }
  if (!workspaceMetadata.isDirectory() || workspaceMetadata.isSymbolicLink()) return null;
  let workspaceReal;
  try {
    workspaceReal = await realpath(spec.workspace);
  } catch {
    return null;
  }
  const containedFile = async (relative) => {
    const absolute = join(workspaceReal, relative);
    let metadata;
    try {
      metadata = await lstat(absolute);
    } catch {
      return null;
    }
    if (!metadata.isFile() || metadata.isSymbolicLink()) return null;
    let real;
    try {
      real = await realpath(absolute);
    } catch {
      return null;
    }
    if (!real.startsWith(workspaceReal + sep)) return null;
    return real;
  };
  const entryReal = await containedFile(spec.entryHtml);
  if (entryReal === null) return null;
  const assetReals = [];
  for (const asset of spec.allowedAssets) {
    const real = await containedFile(asset);
    if (real === null) return null;
    assetReals.push(real);
  }
  const framesDirectory = join(workspaceReal, SANDBOX_FRAMES_DIRECTORY);
  try {
    await lstat(framesDirectory);
    return null;
  } catch {
    // The frames directory must not exist yet.
  }
  return { assetReals, entryReal, framesDirectory, workspaceReal };
}

function validSandboxCommand(bootstrap, command) {
  if (!hasExactKeys(command, [
    "authenticationProof", "command", "jobId", "protocolVersion", "sandbox", "workerKind",
  ])) return false;
  if (command.command !== "worker.render.sandbox"
      || command.protocolVersion !== PROTOCOL_VERSION
      || command.workerKind !== "node"
      || typeof command.jobId !== "string"
      || !UUID_V4_PATTERN.test(command.jobId)) return false;
  const expected = proof(
    bootstrap.localSessionToken,
    COMMAND_DOMAIN,
    [
      "worker.render.sandbox", "node", PROTOCOL_VERSION, command.jobId,
      canonicalJson(command.sandbox),
    ],
    "atvwc1.",
  );
  if (typeof command.authenticationProof !== "string") return false;
  const actualBytes = Buffer.from(command.authenticationProof);
  const expectedBytes = Buffer.from(expected);
  return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes);
}

function runBrowserProcess(executablePath, browserArguments, environment, timeoutMs) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(executablePath, browserArguments, {
        detached: true,
        env: environment,
        stdio: ["ignore", "pipe", "ignore"],
      });
    } catch {
      resolve({ status: "error" });
      return;
    }
    let stdout = "";
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      if (stdout.length < 4096) stdout += chunk;
    });
    let settled = false;
    const timer = setTimeout(() => {
      settled = true;
      killBrowserProcessGroup(child);
      resolve({ status: "timeout" });
    }, timeoutMs);
    child.on("error", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ status: "error" });
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ status: "exit", code, stdout });
    });
  });
}

/**
 * Launch a headless browser bound to `--remote-debugging-pipe` (never a
 * listening debug port), confirm the running product version over CDP and
 * shut it down with `Browser.close`. The process group is killed on timeout.
 */
function runHeadlessProbe(executablePath, browserArguments, environment, timeoutMs) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(executablePath, browserArguments, {
        detached: true,
        env: environment,
        stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"],
      });
    } catch {
      resolve({ status: "error" });
      return;
    }
    let settled = false;
    let buffer = "";
    let product = null;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const killProcessGroup = () => killBrowserProcessGroup(child);
    const timer = setTimeout(() => {
      killProcessGroup();
      finish({ status: "timeout" });
    }, timeoutMs);
    child.on("error", () => finish({ status: "error" }));
    child.stdio[3].on("error", () => {});
    child.stdio[4].on("error", () => {});
    child.stdio[4].setEncoding("utf8");
    child.stdio[4].on("data", (chunk) => {
      buffer += chunk;
      for (;;) {
        const separator = buffer.indexOf("\0");
        if (separator < 0) {
          if (buffer.length > MAX_PROTOCOL_RESPONSE_BYTES) {
            killProcessGroup();
            finish({ status: "protocol" });
          }
          return;
        }
        let response;
        try {
          response = JSON.parse(buffer.slice(0, separator));
        } catch {
          killProcessGroup();
          finish({ status: "protocol" });
          return;
        }
        buffer = buffer.slice(separator + 1);
        if (product === null) {
          if (response?.id !== 1) {
            killProcessGroup();
            finish({ status: "protocol" });
            return;
          }
          const value = response?.result?.product;
          product = typeof value === "string" ? value : "";
          child.stdio[3].write(`${fixedJson({ id: 2, method: "Browser.close" })}\0`);
          continue;
        }
        if (response?.id !== 2) continue;
        // Windows Chrome acknowledges Browser.close but retains the root
        // process while its inherited CDP pipe handles remain open. Kill the
        // still-live owned tree only after that acknowledgement, then report
        // the already authenticated Browser.getVersion result.
        if (process.platform === "win32") {
          killProcessGroup();
          finish({ status: "exit", product });
        }
        return;
      }
    });
    child.on("close", (code) => {
      if (product === null || code !== 0) {
        finish({ status: "protocol" });
        return;
      }
      finish({ status: "exit", product });
    });
    child.stdio[3].write(`${fixedJson({ id: 1, method: "Browser.getVersion" })}\0`);
  });
}

/**
 * Launch the single Rust-verified Chromium as an independent headless
 * process inside a per-RenderJob temporary directory. No upstream
 * download, system browser discovery or cache fallback is ever consulted:
 * the only executable this worker may run is the bootstrap path.
 */
async function renderVerify(renderBrowser, jobId) {
  if (renderBrowser === null) return { failed: "render_browser_unavailable" };
  const jobDirectory = await mkdtemp(join(tmpdir(), `${RENDER_JOB_PREFIX}${jobId}-`));
  try {
    if ((await executableFileMetadata(renderBrowser.executablePath)) === null) {
      return { failed: "render_browser_unusable" };
    }
    const environment = renderProcessEnvironment(jobDirectory);
    const timeoutMs = renderBrowser.launchTimeoutSeconds * 1000;
    // Chrome for Testing on Windows is a GUI-subsystem executable: `--version`
    // may start the full browser and never produce stdout. The authenticated
    // CDP Browser.getVersion response below is the authoritative live-binary
    // check on that platform. POSIX keeps the additional standalone probe.
    if (process.platform !== "win32") {
      const version = await runBrowserProcess(
        renderBrowser.executablePath,
        ["--version"],
        environment,
        timeoutMs,
      );
      if (version.status === "timeout") return { failed: "render_timeout" };
      if (version.status !== "exit" || version.code !== 0) {
        return { failed: "render_browser_unusable" };
      }
      const major = Number(CHROMIUM_VERSION_PATTERN.exec(version.stdout)?.[1]);
      if (!Number.isInteger(major)) return { failed: "render_browser_unusable" };
      if (major !== renderBrowser.chromiumMajor) {
        return { failed: "chromium_major_mismatch" };
      }
    }
    const probe = await runHeadlessProbe(
      renderBrowser.executablePath,
      [
        "--headless",
        "--remote-debugging-pipe",
        "--use-mock-keychain",
        "--password-store=basic",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-component-update",
        "--disable-background-networking",
        "--no-pings",
        `--user-data-dir=${join(jobDirectory, "profile")}`,
        `--crash-dumps-dir=${join(jobDirectory, "crashes")}`,
        "--window-size=64,64",
        "about:blank",
      ],
      environment,
      timeoutMs,
    );
    if (probe.status === "timeout") return { failed: "render_timeout" };
    if (probe.status === "error") return { failed: "render_browser_unusable" };
    if (probe.status !== "exit") return { failed: "render_protocol_invalid" };
    const headlessMajor = Number(CHROMIUM_VERSION_PATTERN.exec(probe.product)?.[1]);
    if (!Number.isInteger(headlessMajor)) return { failed: "render_protocol_invalid" };
    if (headlessMajor !== renderBrowser.chromiumMajor) {
      return { failed: "chromium_major_mismatch" };
    }
    return { chromiumMajor: headlessMajor };
  } finally {
    await rm(jobDirectory, {
      force: true,
      maxRetries: process.platform === "win32" ? 20 : 0,
      recursive: true,
      retryDelay: 100,
    });
  }
}

function parseCpuSeconds(text) {
  let days = 0;
  let rest = text;
  const dash = text.indexOf("-");
  if (dash >= 0) {
    days = Number(text.slice(0, dash));
    rest = text.slice(dash + 1);
  }
  const parts = rest.split(":").map(Number);
  if (!Number.isFinite(days) || parts.some((value) => !Number.isFinite(value))) return null;
  return parts.reduce((total, value) => total * 60 + value, 0) + days * 86400;
}

/** Sum resident memory (KB) and cumulative CPU seconds over one Windows tree. */
function sampleWindowsProcessTree(rootProcessId) {
  return new Promise((resolve) => {
    const systemRoot = process.env.SystemRoot ?? process.env.WINDIR;
    if (typeof systemRoot !== "string" || systemRoot.length === 0) {
      resolve(null);
      return;
    }
    const command = [
      "$ErrorActionPreference='Stop';",
      "Get-CimInstance -ClassName Win32_Process | ",
      "Select-Object ProcessId,ParentProcessId,KernelModeTime,UserModeTime,WorkingSetSize | ",
      "ConvertTo-Json -Compress",
    ].join("");
    execFile(
      join(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
      ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
      { maxBuffer: 8 * 1024 * 1024, windowsHide: true },
      (error, stdout) => {
        if (error) {
          resolve(null);
          return;
        }
        let parsed;
        try {
          parsed = JSON.parse(stdout);
        } catch {
          resolve(null);
          return;
        }
        const processes = (Array.isArray(parsed) ? parsed : [parsed]).map((entry) => ({
          cpuSeconds: (
            Number(entry?.KernelModeTime ?? 0) + Number(entry?.UserModeTime ?? 0)
          ) / 10_000_000,
          parentProcessId: Number(entry?.ParentProcessId),
          processId: Number(entry?.ProcessId),
          workingSetBytes: Number(entry?.WorkingSetSize),
        }));
        const descendants = new Set([rootProcessId]);
        let changed = true;
        while (changed) {
          changed = false;
          for (const entry of processes) {
            if (
              Number.isInteger(entry.processId)
              && descendants.has(entry.parentProcessId)
              && !descendants.has(entry.processId)
            ) {
              descendants.add(entry.processId);
              changed = true;
            }
          }
        }
        let found = false;
        let cpuSeconds = 0;
        let rssKilobytes = 0;
        for (const entry of processes) {
          if (!descendants.has(entry.processId)) continue;
          if (
            !Number.isFinite(entry.cpuSeconds)
            || !Number.isFinite(entry.workingSetBytes)
          ) continue;
          found = true;
          cpuSeconds += entry.cpuSeconds;
          rssKilobytes += entry.workingSetBytes / 1024;
        }
        resolve(found ? { cpuSeconds, rssKilobytes } : null);
      },
    );
  });
}

/** Sum resident memory (KB) and cumulative CPU seconds over one owned tree. */
function sampleProcessGroup(groupId) {
  if (process.platform === "win32") return sampleWindowsProcessTree(groupId);
  return new Promise((resolve) => {
    execFile(
      "/bin/ps",
      ["-axo", "pgid=,rss=,time="],
      { maxBuffer: 8 * 1024 * 1024 },
      (error, stdout) => {
        if (error) {
          resolve(null);
          return;
        }
        let rssKilobytes = 0;
        let cpuSeconds = 0;
        let found = false;
        for (const line of stdout.split("\n")) {
          const fields = line.trim().split(/\s+/);
          if (fields.length < 3 || Number(fields[0]) !== groupId) continue;
          const rss = Number(fields[1]);
          const cpu = parseCpuSeconds(fields[2]);
          if (!Number.isFinite(rss) || cpu === null) continue;
          found = true;
          rssKilobytes += rss;
          cpuSeconds += cpu;
        }
        resolve(found ? { cpuSeconds, rssKilobytes } : null);
      },
    );
  });
}

/** Minimal CDP client over the `--remote-debugging-pipe` file descriptors. */
class SandboxCdpPipe {
  constructor(child, onEvent, onProtocolFailure) {
    this.child = child;
    this.nextId = 0;
    this.pending = new Map();
    this.buffer = "";
    this.onEvent = onEvent;
    this.onProtocolFailure = onProtocolFailure;
    child.stdio[3].on("error", () => {});
    child.stdio[4].on("error", () => {});
    child.stdio[4].setEncoding("utf8");
    child.stdio[4].on("data", (chunk) => this.consume(chunk));
  }

  consume(chunk) {
    this.buffer += chunk;
    for (;;) {
      const separator = this.buffer.indexOf("\0");
      if (separator < 0) {
        if (this.buffer.length > SANDBOX_MESSAGE_LIMIT_BYTES) this.onProtocolFailure();
        return;
      }
      const raw = this.buffer.slice(0, separator);
      this.buffer = this.buffer.slice(separator + 1);
      let message;
      try {
        message = JSON.parse(raw);
      } catch {
        this.onProtocolFailure();
        return;
      }
      if (message === null || typeof message !== "object") {
        this.onProtocolFailure();
        return;
      }
      if (Number.isInteger(message.id) && this.pending.has(message.id)) {
        const resolvePending = this.pending.get(message.id);
        this.pending.delete(message.id);
        resolvePending(message);
      } else if (typeof message.method === "string") {
        this.onEvent(message);
      }
    }
  }

  send(method, params, sessionId) {
    this.nextId += 1;
    const id = this.nextId;
    const message = { id, method };
    if (params !== undefined) message.params = params;
    if (sessionId !== undefined) message.sessionId = sessionId;
    return new Promise((resolvePending) => {
      this.pending.set(id, resolvePending);
      try {
        this.child.stdio[3].write(`${fixedJson(message)}\0`);
      } catch {
        // A dead pipe surfaces through the close handler.
      }
    });
  }
}

/**
 * One sandboxed render session inside the real headless browser: default
 * offline, request allowlist limited to the entry document and declared
 * assets, navigation/download/popup/dialog interception, wall-clock, CPU,
 * memory and output-byte budgets, and process-group kill on every exit.
 */
function runSandboxBrowser(renderBrowser, spec, resolved, jobDirectory, environment) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(renderBrowser.executablePath, [
        "--headless",
        "--remote-debugging-pipe",
        "--use-mock-keychain",
        "--password-store=basic",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-component-update",
        "--disable-background-networking",
        "--no-pings",
        "--block-new-web-contents",
        "--deny-permission-prompts",
        "--dns-prefetch-disable",
        // Byte-identical frames across runs: subpixel text antialiasing and the
        // colour profile are host-dependent and can even differ between the
        // first paint and later ones on the same host, which is how the same
        // frame index hashed differently between two Windows runs.
        "--disable-lcd-text",
        "--disable-font-subpixel-positioning",
        "--force-color-profile=srgb",
        "--hide-scrollbars",
        "--disable-features=NetworkPrediction,PreconnectToSearch,OptimizationHints",
        // Default disconnected: route every http/https/ws connection to a dead
        // loopback proxy (local file:// is never proxied). Nothing reaches a
        // real host even as a speculative preconnect. `<-loopback>` forces even
        // loopback URLs through the dead proxy instead of bypassing it.
        "--proxy-server=127.0.0.1:9",
        "--proxy-bypass-list=<-loopback>",
        `--user-data-dir=${join(jobDirectory, "profile")}`,
        `--crash-dumps-dir=${join(jobDirectory, "crashes")}`,
        `--window-size=${RENDER_VIEWPORT_WIDTH},${RENDER_VIEWPORT_HEIGHT}`,
        "about:blank",
      ], {
        detached: true,
        env: environment,
        stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"],
      });
    } catch {
      resolve({ status: "unusable" });
      return;
    }
    let settled = false;
    let closing = false;
    const counters = {
      blockedDialogs: 0,
      blockedDownloads: 0,
      blockedNavigations: 0,
      blockedPopups: 0,
      blockedRequests: 0,
    };
    let framesCaptured = 0;
    let outputBytes = 0;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(wallTimer);
      clearInterval(monitorTimer);
      killBrowserProcessGroup(child);
      resolve(value);
    };
    const wallTimer = setTimeout(() => finish({ status: "timeout" }), spec.maxDurationSeconds * 1000);
    let sampling = false;
    const monitorTimer = setInterval(async () => {
      if (settled || sampling) return;
      sampling = true;
      const sample = await sampleProcessGroup(child.pid);
      sampling = false;
      if (settled || sample === null) return;
      if (
        sample.cpuSeconds > spec.maxCpuSeconds
        || sample.rssKilobytes > spec.maxMemoryMegabytes * 1024
      ) finish({ status: "resource" });
    }, RESOURCE_MONITOR_INTERVAL_MS);
    child.on("error", () => finish({ status: "unusable" }));
    child.on("close", (code) => {
      if (closing && code === 0) {
        finish({ counters, framesCaptured, outputBytes, status: "complete" });
      } else {
        finish({ status: "protocol" });
      }
    });

    const entryUrl = pathToFileURL(resolved.entryReal).href;
    const allowedPaths = new Set([resolved.entryReal, ...resolved.assetReals]);
    let resolveLoad;
    const loadFired = new Promise((resolvePending) => {
      resolveLoad = resolvePending;
    });
    const allowedFileRequest = (url, isDocument) => {
      let parsed;
      try {
        parsed = new URL(url);
      } catch {
        return false;
      }
      if (parsed.protocol !== "file:") return false;
      let path;
      try {
        path = fileURLToPath(parsed);
      } catch {
        return false;
      }
      return isDocument ? path === resolved.entryReal : allowedPaths.has(path);
    };
    const onEvent = (message) => {
      const params = message.params ?? {};
      switch (message.method) {
        case "Fetch.requestPaused": {
          const url = typeof params.request?.url === "string" ? params.request.url : "";
          const isDocument = params.resourceType === "Document";
          if (allowedFileRequest(url, isDocument)) {
            pipe.send("Fetch.continueRequest", { requestId: params.requestId }, message.sessionId);
          } else {
            if (isDocument) counters.blockedNavigations += 1;
            else counters.blockedRequests += 1;
            pipe.send(
              "Fetch.failRequest",
              { errorReason: "AccessDenied", requestId: params.requestId },
              message.sessionId,
            );
          }
          return;
        }
        case "Browser.downloadWillBegin":
          counters.blockedDownloads += 1;
          return;
        case "Page.windowOpen":
          // `--block-new-web-contents` denies the popup; this event proves the
          // attempt was made and refused.
          counters.blockedPopups += 1;
          return;
        case "Target.attachedToTarget":
          // A child frame or worker attached: intercept it before it can run.
          // `waitForDebuggerOnStart` holds it paused until interception is in
          // place, closing the out-of-process-iframe network-escape hole.
          installInterception(params.sessionId).then(
            () => pipe.send("Runtime.runIfWaitingForDebugger", undefined, params.sessionId),
            () => finish({ status: "protocol" }),
          );
          return;
        case "Page.javascriptDialogOpening":
          counters.blockedDialogs += 1;
          pipe.send("Page.handleJavaScriptDialog", { accept: false }, message.sessionId);
          return;
        case "Page.loadEventFired":
          resolveLoad();
          return;
        default:
      }
    };
    const pipe = new SandboxCdpPipe(child, onEvent, () => finish({ status: "protocol" }));

    // Install the full interception boundary on one session (the root page and
    // every auto-attached child): default offline, request allowlist, dialog
    // handling, and recursive auto-attach so descendants are intercepted too.
    const installInterception = async (sessionId) => {
      await pipe.send("Fetch.enable", { patterns: [{ urlPattern: "*" }] }, sessionId);
      await pipe.send("Network.enable", {}, sessionId);
      await pipe.send("Network.emulateNetworkConditions", {
        downloadThroughput: -1,
        latency: 0,
        offline: true,
        uploadThroughput: -1,
      }, sessionId);
      await pipe.send("Page.enable", {}, sessionId);
      await pipe.send("Page.setDownloadBehavior", { behavior: "deny" }, sessionId);
      await pipe.send("Target.setAutoAttach", {
        autoAttach: true,
        flatten: true,
        waitForDebuggerOnStart: true,
      }, sessionId);
    };

    (async () => {
      const version = await pipe.send("Browser.getVersion");
      const product = version?.result?.product;
      const major = Number(
        CHROMIUM_VERSION_PATTERN.exec(typeof product === "string" ? product : "")?.[1],
      );
      if (!Number.isInteger(major)) {
        finish({ status: "protocol" });
        return;
      }
      if (major !== renderBrowser.chromiumMajor) {
        finish({ status: "mismatch" });
        return;
      }
      await pipe.send("Browser.setDownloadBehavior", { behavior: "deny", eventsEnabled: true });
      const created = await pipe.send("Target.createTarget", { url: "about:blank" });
      const targetId = created?.result?.targetId;
      if (typeof targetId !== "string") {
        finish({ status: "protocol" });
        return;
      }
      const attached = await pipe.send("Target.attachToTarget", { flatten: true, targetId });
      const sessionId = attached?.result?.sessionId;
      if (typeof sessionId !== "string") {
        finish({ status: "protocol" });
        return;
      }
      await installInterception(sessionId);
      const metrics = await pipe.send("Emulation.setDeviceMetricsOverride", {
        width: RENDER_VIEWPORT_WIDTH,
        height: RENDER_VIEWPORT_HEIGHT,
        deviceScaleFactor: 1,
        mobile: false,
      }, sessionId);
      if (metrics?.error !== undefined) {
        finish({ status: "protocol" });
        return;
      }
      await pipe.send("Page.navigate", { url: entryUrl }, sessionId);
      await loadFired;
      // `load` only means the resources arrived. Web fonts are rasterised
      // lazily, so capturing before they are ready lets the first frames of a
      // run differ from the same frames of the next one. Observed on Windows,
      // where the font backend differs from macOS.
      const fontsReady = await pipe.send("Runtime.evaluate", {
        expression: "document.fonts.ready.then(() => true)",
        awaitPromise: true,
        returnByValue: true,
      }, sessionId);
      if (fontsReady?.result?.exceptionDetails !== undefined) {
        finish({ status: "protocol" });
        return;
      }
      const timelineProbe = await pipe.send("Runtime.evaluate", {
        expression: `(() => {
          const root = document.querySelector('[data-composition-id][data-duration]');
          const duration = Number(root?.getAttribute('data-duration'));
          const timelines = Object.values(window.__timelines ?? {})
            .filter((timeline) => timeline && typeof timeline.seek === 'function');
          return {
            duration: Number.isFinite(duration) && duration > 0 ? duration : 0,
            timelineCount: timelines.length,
          };
        })()`,
        returnByValue: true,
      }, sessionId);
      const timelineMetadata = timelineProbe?.result?.result?.value;
      const seekableDuration = (
        Number.isFinite(timelineMetadata?.duration)
        && timelineMetadata.duration > 0
        && Number.isInteger(timelineMetadata?.timelineCount)
        && timelineMetadata.timelineCount > 0
      ) ? timelineMetadata.duration : 0;
      // Warm up before the first kept frame. A composition's first paint
      // triggers lazy work — image decode, canvas and SVG initialisation —
      // whose completion drifts across the next few frames. That is why an
      // early frame index still hashed differently between two Windows runs
      // after fonts and compositing were already awaited.
      // Warm up until the composition stops changing, not for a fixed number
      // of frames. A single settle is enough on an idle machine but not on a
      // busy one: measured on Windows, this render is byte-identical across
      // runs in isolation yet drifted in its early frames when it followed a
      // twelve-style sweep. Capturing until two probes agree removes that
      // dependency on how loaded the host happens to be.
      let previousProbe = null;
      let stable = false;
      for (let attempt = 0; attempt < WARM_UP_STABLE_ATTEMPTS; attempt += 1) {
        const warmed = await pipe.send("Runtime.evaluate", {
          expression: `(async () => {
            for (const timeline of Object.values(window.__timelines ?? {})) {
              if (timeline && typeof timeline.seek === 'function') timeline.seek(0, false);
            }
            ${WARM_UP_EXPRESSION_BODY}
          })()`,
          awaitPromise: true,
          returnByValue: true,
        }, sessionId);
        if (warmed?.result?.exceptionDetails !== undefined) {
          finish({ status: "protocol" });
          return;
        }
        const probe = await pipe.send("Page.captureScreenshot", { format: "png" }, sessionId);
        const data = probe?.result?.data;
        if (typeof data !== "string") {
          finish({ status: "protocol" });
          return;
        }
        if (data === previousProbe) {
          stable = true;
          break;
        }
        previousProbe = data;
      }
      if (!stable) {
        // The page never settled within the warm-up budget; a render that
        // cannot be reproduced must not be reported as a successful one.
        finish({ status: "timeout" });
        return;
      }
      for (let index = 1; index <= spec.frameCount; index += 1) {
        try {
          await access(join(resolved.workspaceReal, SANDBOX_CANCEL_FILE));
          finish({ status: "cancelled" });
          return;
        } catch {
          // The fixed cancellation marker is absent; continue this frame.
        }
        if (seekableDuration > 0) {
          const time = seekableDuration * (index - 1) / spec.frameCount;
          const seek = await pipe.send("Runtime.evaluate", {
            expression: `(() => {
              const time = ${JSON.stringify(time)};
              for (const timeline of Object.values(window.__timelines ?? {})) {
                if (timeline && typeof timeline.seek === 'function') timeline.seek(time, false);
              }
            })()`,
            returnByValue: true,
          }, sessionId);
          if (seek?.result?.exceptionDetails !== undefined) {
            finish({ status: "protocol" });
            return;
          }
          // A seek updates style synchronously but decoding and compositing
          // are not: wait for the images this frame needs and for two
          // animation frames, so the seeked state is actually rastered before
          // it is captured.
          const settled = await pipe.send("Runtime.evaluate", {
            expression: `(async () => { ${COMPOSITED_EXPRESSION_BODY} })()`,
            awaitPromise: true,
            returnByValue: true,
          }, sessionId);
          if (settled?.result?.exceptionDetails !== undefined) {
            finish({ status: "protocol" });
            return;
          }
        }
        const shot = await pipe.send("Page.captureScreenshot", { format: "png" }, sessionId);
        const data = shot?.result?.data;
        if (typeof data !== "string") {
          finish({ status: "protocol" });
          return;
        }
        const bytes = Buffer.from(data, "base64");
        if (outputBytes + bytes.length > spec.maxOutputBytes) {
          finish({ status: "output" });
          return;
        }
        await writeFile(
          join(resolved.framesDirectory, `frame-${String(index).padStart(5, "0")}.png`),
          bytes,
        );
        outputBytes += bytes.length;
        framesCaptured = index;
      }
      closing = true;
      const closed = await pipe.send("Browser.close");
      if (process.platform === "win32") {
        if (!Number.isInteger(closed?.id) || closed?.error !== undefined) {
          finish({ status: "protocol" });
          return;
        }
        finish({ counters, framesCaptured, outputBytes, status: "complete" });
      }
    })().catch(() => finish({ status: "protocol" }));
  });
}

/**
 * Run the sandboxed render for one RenderJob. The workspace boundary is
 * resolved before any browser process exists, the executable major is
 * re-verified, and the frames directory is removed on every failure path.
 */
async function renderSandbox(renderBrowser, jobId, spec) {
  if (renderBrowser === null) return { failed: "render_browser_unavailable" };
  const resolved = await resolveSandboxWorkspace(spec);
  if (resolved === null) return { failed: "render_workspace_invalid" };
  if ((await executableFileMetadata(renderBrowser.executablePath)) === null) {
    return { failed: "render_browser_unusable" };
  }
  const jobDirectory = await mkdtemp(join(tmpdir(), `${RENDER_JOB_PREFIX}${jobId}-`));
  let succeeded = false;
  try {
    const environment = renderProcessEnvironment(jobDirectory);
    if (process.platform !== "win32") {
      const version = await runBrowserProcess(
        renderBrowser.executablePath,
        ["--version"],
        environment,
        renderBrowser.launchTimeoutSeconds * 1000,
      );
      if (version.status === "timeout") return { failed: "render_timeout" };
      if (version.status !== "exit" || version.code !== 0) {
        return { failed: "render_browser_unusable" };
      }
      const major = Number(CHROMIUM_VERSION_PATTERN.exec(version.stdout)?.[1]);
      if (!Number.isInteger(major)) return { failed: "render_browser_unusable" };
      if (major !== renderBrowser.chromiumMajor) {
        return { failed: "chromium_major_mismatch" };
      }
    }
    await mkdir(resolved.framesDirectory);
    const outcome = await runSandboxBrowser(renderBrowser, spec, resolved, jobDirectory, environment);
    if (outcome.status !== "complete") return { failed: SANDBOX_FAILURES[outcome.status] };
    succeeded = true;
    return { sandboxed: { chromiumMajor: renderBrowser.chromiumMajor, ...outcome } };
  } finally {
    await rm(jobDirectory, {
      force: true,
      maxRetries: process.platform === "win32" ? 20 : 0,
      recursive: true,
      retryDelay: 100,
    });
    if (!succeeded) await rm(resolved.framesDirectory, { recursive: true, force: true });
  }
}

function renderEventLine(bootstrap, jobId, result) {
  if (result.failed !== undefined) {
    return fixedJson({
      authenticationProof: eventProof(bootstrap, "worker.render.failed", `${jobId}\0${result.failed}`),
      event: "worker.render.failed",
      jobId,
      protocolVersion: PROTOCOL_VERSION,
      reasonCode: result.failed,
      workerKind: "node",
      workerVersion: WORKER_VERSION,
    });
  }
  return fixedJson({
    authenticationProof: eventProof(
      bootstrap,
      "worker.render.verified",
      `${jobId}\0${result.chromiumMajor}`,
    ),
    chromiumMajor: result.chromiumMajor,
    event: "worker.render.verified",
    jobId,
    protocolVersion: PROTOCOL_VERSION,
    workerKind: "node",
    workerVersion: WORKER_VERSION,
  });
}

function sandboxEventLine(bootstrap, jobId, result) {
  if (result.failed !== undefined) return renderEventLine(bootstrap, jobId, result);
  const sandboxed = result.sandboxed;
  const detail = [
    jobId,
    sandboxed.chromiumMajor,
    sandboxed.framesCaptured,
    sandboxed.outputBytes,
    sandboxed.counters.blockedRequests,
    sandboxed.counters.blockedNavigations,
    sandboxed.counters.blockedDownloads,
    sandboxed.counters.blockedPopups,
    sandboxed.counters.blockedDialogs,
  ].join("\0");
  return fixedJson({
    authenticationProof: eventProof(bootstrap, "worker.render.sandboxed", detail),
    blockedDialogs: sandboxed.counters.blockedDialogs,
    blockedDownloads: sandboxed.counters.blockedDownloads,
    blockedNavigations: sandboxed.counters.blockedNavigations,
    blockedPopups: sandboxed.counters.blockedPopups,
    blockedRequests: sandboxed.counters.blockedRequests,
    chromiumMajor: sandboxed.chromiumMajor,
    event: "worker.render.sandboxed",
    framesCaptured: sandboxed.framesCaptured,
    jobId,
    outputBytes: sandboxed.outputBytes,
    protocolVersion: PROTOCOL_VERSION,
    workerKind: "node",
    workerVersion: WORKER_VERSION,
  });
}

async function main() {
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const iterator = lines[Symbol.asyncIterator]();
  const first = await iterator.next();
  if (first.done) {
    process.stderr.write("Motion composition worker command is required\n");
    return 64;
  }
  let bootstrap;
  try {
    bootstrap = await parseBootstrap(first.value);
  } catch {
    process.stderr.write(`${FIXED_BOOTSTRAP_ERROR}\n`);
    return 65;
  }

  const server = createServer((request, response) => {
    const authorized = request.headers.authorization === `Bearer ${bootstrap.localSessionToken}`;
    if (request.method !== "GET" || request.url !== "/health" || !authorized) {
      response.writeHead(401, { "content-length": "0", connection: "close" });
      response.end();
      return;
    }
    const port = server.address().port;
    const body = fixedJson({
      authenticationProof: eventProof(bootstrap, "worker.health", String(port)),
      event: "worker.health",
      protocolVersion: PROTOCOL_VERSION,
      workerKind: "node",
      workerVersion: WORKER_VERSION,
      port,
    });
    response.writeHead(200, {
      "content-type": "application/json",
      "content-length": String(Buffer.byteLength(body)),
      connection: "close",
    });
    response.end(body);
  });
  server.on("clientError", (_error, socket) => socket.destroy());
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  process.stdout.write(`${fixedJson({
    authenticationProof: eventProof(bootstrap, "worker.ready", String(port)),
    event: "worker.ready",
    protocolVersion: PROTOCOL_VERSION,
    scriptModelId: null,
    webUiAuthenticationProof: null,
    webUiPath: null,
    webUiPort: null,
    workerKind: "node",
    workerVersion: WORKER_VERSION,
    port,
  })}\n`);

  for await (const line of { [Symbol.asyncIterator]: () => iterator }) {
    if (Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) continue;
    let command;
    try {
      command = JSON.parse(line);
    } catch {
      continue;
    }
    if (validCommand(bootstrap, command, "worker.cancel")) {
      process.stdout.write(`${fixedJson({
        authenticationProof: eventProof(bootstrap, "worker.cancelled", command.jobId),
        event: "worker.cancelled",
        jobId: command.jobId,
        protocolVersion: PROTOCOL_VERSION,
        workerKind: "node",
        workerVersion: WORKER_VERSION,
      })}\n`);
      continue;
    }
    if (validCommand(bootstrap, command, "worker.render.verify")) {
      const result = await renderVerify(bootstrap.renderBrowser, command.jobId);
      process.stdout.write(`${renderEventLine(bootstrap, command.jobId, result)}\n`);
      continue;
    }
    if (validSandboxCommand(bootstrap, command)) {
      const result = validSandboxSpec(command.sandbox)
        ? await renderSandbox(bootstrap.renderBrowser, command.jobId, command.sandbox)
        : { failed: "render_sandbox_invalid" };
      process.stdout.write(`${sandboxEventLine(bootstrap, command.jobId, result)}\n`);
    }
  }
  await new Promise((resolve) => server.close(resolve));
  return 0;
}

main().then((code) => { process.exitCode = code; }).catch(() => {
  process.stderr.write(`${FIXED_BOOTSTRAP_ERROR}\n`);
  process.exitCode = 70;
});
