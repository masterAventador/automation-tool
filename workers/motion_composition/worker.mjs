#!/usr/bin/env node
/** Isolated process boundary for the motion-composition runtime. */

import { execFile, spawn, spawnSync } from "node:child_process";
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
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
// The viewport travels with the render request, because more than one kind of
// composition is rendered now. `composition_template` draws on a 640x360 stage
// its whole type scale is written for; a catalog part is an independent
// composition that declares its own — 105 of the frozen catalog's parts are
// 1920x1080, three are 1080x1920 portrait and one is 1440x2560. Rendering a
// part on the template's stage captures the top-left corner of it, which is the
// incident `contracts/video/motion-render-canvas.v1.json` records under
// `rationale.problem`: a valid MP4 that was a still image, invisible to both
// the authoring gates and this sandbox because neither knew the other's number.
//
// What stays fixed here are the bounds. They mirror `requestedCanvas` in that
// contract; `frontend/tests/motion-render-canvas-per-render.test.mjs` is what
// keeps the two from drifting.
//
// The created CDP target does not inherit `--window-size`, so the render
// session also forces the requested metrics through the DevTools protocol.
const CANVAS_WIDTH_MINIMUM = 320;
const CANVAS_WIDTH_MAXIMUM = 2560;
const CANVAS_HEIGHT_MINIMUM = 320;
const CANVAS_HEIGHT_MAXIMUM = 2560;
const CANVAS_DEVICE_SCALE_FACTOR_MINIMUM = 1;
const CANVAS_DEVICE_SCALE_FACTOR_MAXIMUM = 3;
// Output pixels, not CSS pixels: a captured PNG costs what it measures, and the
// per-frame budget in `motion-render-sandbox-budget.v1.json` is written against
// that. 1920x1080 at factor 1 and 640x360 at factor 2 both sit inside it.
const CANVAS_OUTPUT_PIXELS_MAXIMUM = 3686400;
const MAX_PROTOCOL_RESPONSE_BYTES = 64 * 1024;
const SANDBOX_FRAMES_MAXIMUM = 600;
// Wall clock is the stall guard: a hung render is killed at this many seconds.
const SANDBOX_SECONDS_MAXIMUM = 300;
// CPU seconds are a different quantity: they are summed over the whole browser
// process tree, so a render occupying N cores accrues them N times faster than
// wall clock. The admissible CPU budget is therefore the wall-clock budget
// times the highest average core occupancy one render may declare — sharing the
// wall-clock ceiling rejected legitimate multi-core budgets on long renders and,
// on short ones, admitted a figure no host can reach, leaving the guard inert.
// See `contracts/video/motion-render-sandbox-budget.v1.json`.
const SANDBOX_CPU_PARALLELISM_MAXIMUM = 8;
const SANDBOX_MEMORY_MEGABYTES_MINIMUM = 128;
const SANDBOX_MEMORY_MEGABYTES_MAXIMUM = 8192;
const SANDBOX_OUTPUT_BYTES_MAXIMUM = 2147483647;
const SANDBOX_ASSETS_MAXIMUM = 128;
const SANDBOX_RELATIVE_PATH_MAXIMUM = 512;
const SANDBOX_MESSAGE_LIMIT_BYTES = 32 * 1024 * 1024;
const SANDBOX_FRAMES_DIRECTORY = "frames";
// A render is rejected as static only when *every* captured frame is
// byte-identical, and only once there are at least this many frames to
// compare — one frame carries no evidence either way.
//
// Why byte-identity rather than a perceptual threshold, and why "all frames"
// rather than a first/middle/last sample:
//
//   - Anything that moves changes bytes. A one-pixel shift, a fade, a colour
//     step all produce a different PNG, so a real animation cannot trip this.
//     The false-positive surface is exactly "not one pixel changed across the
//     whole duration", which is the defect and not a deliverable: this Worker
//     exists to turn a seekable timeline into a film.
//   - Comparing every frame rather than three samples avoids the opposite
//     mistake. An animation that returns to its opening state — a pulse that
//     completes a whole number of cycles — can have identical first, middle
//     and last frames while moving throughout. Requiring *all* frames to match
//     makes any motion anywhere in the timeline enough to pass.
//   - Byte-identity is trustworthy here because this render is deterministic
//     by construction: fonts are awaited, CSS animations are frozen and driven
//     off the seek time, and the warm-up runs until the page stops changing.
//     Identical bytes therefore mean an identical rendered state, not sampling
//     luck.
//
// The one way a moving composition could still be called static is if all of
// its motion fell between two captures. The authoring side asks for
// `duration * 30` frames, so consecutive captures are 33ms apart and nothing
// visible fits between them; a caller that asked for a handful of frames over
// a long duration would be sampling too coarsely to judge motion at all.
const STATIC_FRAME_COMPARISON_MINIMUM = 2;
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
// CSS animations and transitions advance off the real clock, so a composition
// that uses them renders differently every run no matter how long the capture
// waits. Pausing them and driving `currentTime` from the same instant the
// timeline is seeked to makes the whole frame a pure function of that time —
// and lets the warm-up reach a still image at all.
function frozenAnimationsExpression(timeMilliseconds) {
  return `(() => {
    for (const animation of document.getAnimations()) {
      animation.pause();
      try {
        animation.currentTime = ${JSON.stringify(timeMilliseconds)};
      } catch {
        // A finished or immutable animation keeps whatever it settled on.
      }
    }
  })()`;
}
const RESOURCE_MONITOR_INTERVAL_MS = 300;
/** Rounding slack when comparing a declared window against a declared timeline. */
const FRAME_TIME_TOLERANCE_SECONDS = 0.001;
const SANDBOX_FAILURES = {
  cancelled: "render_cancelled",
  mismatch: "chromium_major_mismatch",
  output: "render_output_exceeded",
  protocol: "render_protocol_invalid",
  resource: "render_resource_exceeded",
  static: "render_static_frames",
  window: "render_window_outside_timeline",
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

function validCanvas(value) {
  if (!hasExactKeys(value, ["deviceScaleFactor", "height", "width"])) return false;
  if (!boundedInteger(value.width, CANVAS_WIDTH_MINIMUM, CANVAS_WIDTH_MAXIMUM)) return false;
  if (!boundedInteger(value.height, CANVAS_HEIGHT_MINIMUM, CANVAS_HEIGHT_MAXIMUM)) return false;
  if (!boundedInteger(
    value.deviceScaleFactor,
    CANVAS_DEVICE_SCALE_FACTOR_MINIMUM,
    CANVAS_DEVICE_SCALE_FACTOR_MAXIMUM,
  )) return false;
  // The product is what a frame costs. Checking the sides alone would admit
  // 2560x2560 at factor 3, which is 59 megapixels a frame.
  const factor = value.deviceScaleFactor;
  return value.width * factor * value.height * factor <= CANVAS_OUTPUT_PIXELS_MAXIMUM;
}


function validSandboxSpec(value) {
  if (!hasExactKeys(value, [
    "allowedAssets", "canvas", "cancelMarker", "entryHtml", "frameCount",
    "maxCpuSeconds", "maxDurationSeconds", "maxMemoryMegabytes", "maxOutputBytes",
    "sourceEndMillis", "sourceStartMillis", "workspace",
  ])) return false;
  // Which stretch of the entry document's own timeline this render covers.
  // Required, not optional: the rule this replaced — spread the page's whole
  // timeline over the frames asked for — is right for a film captured in one
  // pass and silently wrong for a film whose shots share one document, and a
  // caller that omits the window would get exactly that silence back.
  //
  // Whole milliseconds rather than seconds, because the command's HMAC binds to
  // a canonical JSON that three languages have to produce byte for byte, and a
  // float does not survive that: Python writes 0.0 where JSON.stringify writes
  // 0, the proof stops matching and the render command is dropped without a
  // word. Every other number in this spec is an integer for the same reason.
  if (
    !boundedInteger(value.sourceStartMillis, 0, SANDBOX_SECONDS_MAXIMUM * 1000)
    || !boundedInteger(value.sourceEndMillis, 1, SANDBOX_SECONDS_MAXIMUM * 1000)
    || value.sourceEndMillis <= value.sourceStartMillis
  ) return false;
  if (!validCanvas(value.canvas)) return false;
  if (typeof value.workspace !== "string" || !isAbsolute(value.workspace)) return false;
  if (!validSandboxRelativePath(value.entryHtml)) return false;
  // This Worker holds no cancellation name of its own: the caller says which
  // workspace file means "stop", so the two sides cannot hold names that
  // disagree. The key is required rather than optional, so a caller that does
  // not send one gets no render instead of an uncancellable one.
  if (!validSandboxRelativePath(value.cancelMarker)) return false;
  if (
    !Array.isArray(value.allowedAssets)
    || value.allowedAssets.length > SANDBOX_ASSETS_MAXIMUM
    || !value.allowedAssets.every(validSandboxRelativePath)
  ) return false;
  return boundedInteger(value.frameCount, 1, SANDBOX_FRAMES_MAXIMUM)
    && boundedInteger(value.maxDurationSeconds, 1, SANDBOX_SECONDS_MAXIMUM)
    && boundedInteger(
      value.maxCpuSeconds,
      1,
      value.maxDurationSeconds * SANDBOX_CPU_PARALLELISM_MAXIMUM,
    )
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
        "--enable-unsafe-swiftshader",
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
  // Validated by `validCanvas` before this runs.
  const canvas = spec.canvas;
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(renderBrowser.executablePath, [
        "--headless",
        "--remote-debugging-pipe",
        "--use-mock-keychain",
        "--password-store=basic",
        "--disable-gpu",
        // Without this, a GPU-less Chromium 149 has no WebGL at all: the
        // SwiftShader software fallback is refused, a shader composition takes
        // its no-GL branch and renders one static page, and the static-frame
        // gate below refuses the job. SwiftShader keeps rendering on the CPU,
        // so the byte-identical-frames requirement above still holds.
        "--enable-unsafe-swiftshader",
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
        `--window-size=${canvas.width},${canvas.height}`,
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
    // Motion detection state; see STATIC_FRAME_COMPARISON_MINIMUM.
    let firstFrameDigest = null;
    let sawMovement = false;
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
        width: canvas.width,
        height: canvas.height,
        deviceScaleFactor: canvas.deviceScaleFactor,
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
      // Two independent facts, kept separate on purpose. Whether the document
      // can be seeked depends only on it registering timelines; whether a
      // requested window can be *validated* depends on the document declaring
      // its own length. Conflating them skipped seeking entirely for the 31
      // catalog documents that register a timeline but carry no
      // `data-duration`, so their typing animations were captured as
      // identical stills and the static gate below refused them (BM-16,
      // first seen on `code-snippet-apple-terminal-basic`).
      const seekableTimelineCount = Number.isInteger(timelineMetadata?.timelineCount)
        ? timelineMetadata.timelineCount
        : 0;
      const seekableDuration = (
        Number.isFinite(timelineMetadata?.duration)
        && timelineMetadata.duration > 0
        && seekableTimelineCount > 0
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
            ${frozenAnimationsExpression(0)};
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
      // This shot's own stretch of the timeline, resolved once — it does not
      // change per frame.
      //
      // A window the document cannot satisfy is refused rather than narrowed.
      // Clamping looks harmless and is not: a window of [5s, 9s] against a six
      // second document moves for a quarter of its frames and then holds, so
      // `sawMovement` is set by the early ones, the frame count is right, and
      // the render reports complete over a shot that is three quarters still.
      // Every gate downstream stays green. That is the same "reasonable default
      // downstream" this whole line keeps being bitten by — the Worker is the
      // only layer that knows `seekableDuration`, so it is the only one that
      // can say no.
      const windowStart = spec.sourceStartMillis / 1000;
      const windowEnd = spec.sourceEndMillis / 1000;
      if (seekableDuration > 0 && windowEnd > seekableDuration + FRAME_TIME_TOLERANCE_SECONDS) {
        finish({ status: "window" });
        return;
      }
      for (let index = 1; index <= spec.frameCount; index += 1) {
        try {
          await access(join(resolved.workspaceReal, spec.cancelMarker));
          finish({ status: "cancelled" });
          return;
        } catch {
          // The cancellation marker the caller named is absent; continue.
        }
        if (seekableTimelineCount > 0) {
          const time = windowStart + (windowEnd - windowStart) * (index - 1) / spec.frameCount;
          const seek = await pipe.send("Runtime.evaluate", {
            expression: `(() => {
              const time = ${JSON.stringify(time)};
              for (const timeline of Object.values(window.__timelines ?? {})) {
                if (timeline && typeof timeline.seek === 'function') timeline.seek(time, false);
              }
              ${frozenAnimationsExpression(time * 1000)};
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
        // Remember only whether any frame ever differed from the first, so the
        // check costs one hash per frame and no growing state.
        const digest = createHash("sha256").update(bytes).digest("base64");
        if (firstFrameDigest === null) firstFrameDigest = digest;
        else if (digest !== firstFrameDigest) sawMovement = true;
        outputBytes += bytes.length;
        framesCaptured = index;
      }
      if (!sawMovement && framesCaptured >= STATIC_FRAME_COMPARISON_MINIMUM) {
        finish({ status: "static" });
        return;
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
