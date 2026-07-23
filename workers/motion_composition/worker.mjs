#!/usr/bin/env node
/** Isolated process boundary for the motion-composition runtime. */

import { spawn } from "node:child_process";
import { createHmac, timingSafeEqual } from "node:crypto";
import { lstat, mkdtemp, realpath, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";
import { createInterface } from "node:readline";

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
const MAX_PROTOCOL_RESPONSE_BYTES = 64 * 1024;

function fixedJson(value) {
  return JSON.stringify(value);
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
  if (!metadata.isFile() || metadata.isSymbolicLink() || !(metadata.mode & 0o111)) return null;
  return metadata;
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
    const killProcessGroup = () => {
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
    };
    const timer = setTimeout(() => {
      killProcessGroup();
      finish({ status: "timeout" });
    }, timeoutMs);
    child.on("error", () => finish({ status: "error" }));
    child.stdio[3].on("error", () => {});
    child.stdio[4].on("error", () => {});
    child.stdio[4].setEncoding("utf8");
    child.stdio[4].on("data", (chunk) => {
      if (product !== null) return;
      buffer += chunk;
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
      const value = response?.result?.product;
      product = typeof value === "string" ? value : "";
      child.stdio[3].write(`${fixedJson({ id: 2, method: "Browser.close" })}\0`);
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
    const environment = { HOME: jobDirectory, TMPDIR: jobDirectory };
    const timeoutMs = renderBrowser.launchTimeoutSeconds * 1000;
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
    if (major !== renderBrowser.chromiumMajor) return { failed: "chromium_major_mismatch" };
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
    await rm(jobDirectory, { recursive: true, force: true });
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
    }
  }
  await new Promise((resolve) => server.close(resolve));
  return 0;
}

main().then((code) => { process.exitCode = code; }).catch(() => {
  process.stderr.write(`${FIXED_BOOTSTRAP_ERROR}\n`);
  process.exitCode = 70;
});
