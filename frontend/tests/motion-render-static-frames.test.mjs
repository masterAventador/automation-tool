// A render that never moves must fail loudly instead of shipping a still image.
//
// The observed defect: the packaged authoring reference
// (`vendor/hyperframes/.../minimal-composition.md`) demonstrates the animation
// runtime as a CDN `<script src="https://...gsap...">`. The render sandbox is
// offline by construction, so that tag resolves to nothing, `gsap` stays
// undefined, and the inline setup throws before it can register
// `window.__timelines`. The Worker then finds no seekable timeline, skips the
// per-frame seek entirely (`if (seekableDuration > 0)`), screenshots the same
// untouched first paint `frameCount` times and reports `status: "complete"`.
// Every gate is green and the deliverable is a static picture.
//
// These tests drive the real `workers/motion_composition/worker.mjs` through
// its real stdin protocol against a real Chromium, because the whole point is
// what the Worker *reports* — a unit test of the comparison could not have
// caught the `seekableDuration` short circuit that produces the frames.

import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";
import { createHash, createHmac } from "node:crypto";
import { copyFile, mkdtemp, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const repositoryRoot = new URL("../../", import.meta.url);
const WORKER = fileURLToPath(new URL("workers/motion_composition/worker.mjs", repositoryRoot));

const PROTOCOL_VERSION = "1.0";
const COMMAND_DOMAIN = "automation-tool.video-worker-command.v1\0";
const TOKEN = "b".repeat(64);
const JOB_ID = "7d444840-9dc0-41a2-bcd4-e15b02a4c51e";
const FRAME_COUNT = 4;
const CANCEL_MARKER = JSON.parse(
  await readFile(
    new URL("contracts/video/motion-render-cancel-marker.v1.json", repositoryRoot),
    "utf8",
  ),
).markerFileName;

// Where a real embedded Chromium may already be staged on this machine. The
// acceptance scripts stage one under `.local/`; an explicit path always wins.
const BROWSER_CANDIDATES = [
  ".local/release/build/embedded-browser/chrome-mac-arm64",
  ".local/desktop-e2e/embedded-browser/macos-arm64/chrome-mac-arm64",
  ".local/t44-release-verify/build/embedded-browser/chrome-mac-arm64",
];
const MAC_APP_SUFFIX = "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing";

function locateRenderBrowser() {
  const explicit = process.env.AUTOMATION_TOOL_RENDER_BROWSER;
  if (typeof explicit === "string" && explicit.length > 0 && existsSync(explicit)) {
    return explicit;
  }
  for (const candidate of BROWSER_CANDIDATES) {
    const executable = fileURLToPath(new URL(`${candidate}/${MAC_APP_SUFFIX}`, repositoryRoot));
    if (existsSync(executable)) return executable;
  }
  return null;
}

async function chromiumMajor(executable) {
  const { stdout } = await execFileAsync(executable, ["--version"]);
  const major = Number(/(\d+)\.\d+\.\d+\.\d+/u.exec(stdout)?.[1]);
  assert.ok(Number.isInteger(major), `could not read a Chromium major from ${stdout}`);
  return major;
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function proof(domain, parts, prefix) {
  const mac = createHmac("sha256", Buffer.from(TOKEN, "hex"));
  mac.update(domain);
  mac.update(parts.join("\0"));
  return prefix + mac.digest("base64url");
}

function sandboxCommandLine(sandbox) {
  return JSON.stringify({
    authenticationProof: proof(
      COMMAND_DOMAIN,
      ["worker.render.sandbox", "node", PROTOCOL_VERSION, JOB_ID, canonicalJson(sandbox)],
      "atvwc1.",
    ),
    command: "worker.render.sandbox",
    jobId: JOB_ID,
    protocolVersion: PROTOCOL_VERSION,
    sandbox,
    workerKind: "node",
  });
}

function bootstrapLine(assetRoot, executable, major) {
  return JSON.stringify({
    assetRoot,
    bootstrapVersion: "1",
    enableWebUi: false,
    localSessionToken: TOKEN,
    protocolVersion: PROTOCOL_VERSION,
    renderBrowser: {
      chromiumMajor: major,
      executablePath: executable,
      launchTimeoutSeconds: 30,
    },
    scriptModel: null,
    workerKind: "node",
  });
}

const PAGE_HEAD = `<!doctype html><html lang="en"><head><meta charset="utf-8" />
<style>html,body{margin:0;background:#0b0f14}
#root{position:relative;width:640px;height:360px;overflow:hidden}
#bar{position:absolute;top:0;height:360px;width:60px;background:#ffffff}</style></head><body>
<div id="root" data-composition-id="t86" data-duration="3" data-width="640" data-height="360">
<div class="clip" id="only" data-start="0" data-duration="3" data-track-index="0">
<div id="bar" style="left:0px"></div></div></div>`;

// Registers a seekable timeline that actually repaints, so consecutive frames
// differ. This is what a healthy composition looks like to the Worker.
const MOVING_PAGE = `${PAGE_HEAD}<script>
window.__timelines = { main: { seek(time) {
  document.getElementById("bar").style.left = Math.round(time * 180) + "px";
} } };
</script></body></html>`;

// The defect verbatim: the CDN tag the reference demonstrates cannot load in
// the offline sandbox, so `gsap` is undefined and this throws before
// `window.__timelines` is ever assigned.
const FROZEN_PAGE = `${PAGE_HEAD}<script>
const timeline = gsap.timeline({ paused: true });
timeline.to("#bar", { left: 540, duration: 3 }, 0);
window.__timelines = { main: timeline };
</script></body></html>`;

// Where a real animation runtime may already be staged on this machine, for the
// T92 test below: it renders the *product's own* template, which loads the same
// runtime the App seeds into every RenderJob workspace.
const RUNTIME_ASSET = "runtime/gsap.min.js";
const RUNTIME_CANDIDATES = [
  ".local/offline-motion-deps/catalog/offline-deps/js/gsap-3.14.2/gsap.min.js",
  ".local/release/cargo-target/release/bundle/macos/自动化运营工具.app/Contents/Resources/motion-video-worker/package/runtime/gsap.min.js",
];

function locateAnimationRuntime() {
  const explicit = process.env.AUTOMATION_TOOL_RENDER_RUNTIME;
  if (typeof explicit === "string" && explicit.length > 0 && existsSync(explicit)) {
    return explicit;
  }
  for (const candidate of RUNTIME_CANDIDATES) {
    const staged = fileURLToPath(new URL(candidate, repositoryRoot));
    if (existsSync(staged)) return staged;
  }
  return null;
}

async function renderPage(html, executable, major, assets = [], window = null) {
  const base = await mkdtemp(join(tmpdir(), "t86-static-"));
  const workspace = join(base, "workspace");
  await mkdir(workspace, { recursive: true });
  await writeFile(join(workspace, "entry.html"), html, "utf8");
  for (const asset of assets) {
    const target = join(workspace, asset.relative);
    await mkdir(join(target, ".."), { recursive: true });
    await copyFile(asset.source, target);
  }
  const sandbox = {
    allowedAssets: assets.map((asset) => asset.relative),
    // The stage travels with the request now: these fixtures are template-shaped
    // compositions, so they ask for the template's own canvas. A catalog part
    // asks for the stage it declares — see
    // `contracts/video/motion-render-canvas.v1.json`.
    canvas: { deviceScaleFactor: 2, height: 360, width: 640 },
    // The Worker holds no cancellation name of its own; the caller supplies the
    // declared one. See `contracts/video/motion-render-cancel-marker.v1.json`.
    cancelMarker: CANCEL_MARKER,
    entryHtml: "entry.html",
    frameCount: FRAME_COUNT,
    // Which stretch of the entry's own timeline this render covers. Default is
    // the whole of it, which is what a film captured in one pass asks for.
    sourceStartSeconds: window?.start ?? 0,
    sourceEndSeconds: window?.end ?? TEMPLATE_DURATION,
    maxCpuSeconds: 120,
    maxDurationSeconds: 60,
    // A real Chromium process group idles well above a gigabyte; the budget
    // here only needs to be loose enough that it is not what ends the render.
    maxMemoryMegabytes: 8192,
    maxOutputBytes: 50_000_000,
    workspace,
  };
  const child = spawn(process.execPath, [WORKER], { stdio: ["pipe", "pipe", "pipe"] });
  try {
    const events = [];
    let buffer = "";
    const settled = new Promise((resolve, reject) => {
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        buffer += chunk;
        let newline = buffer.indexOf("\n");
        while (newline >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          const event = JSON.parse(line);
          events.push(event);
          if (event.event !== "worker.ready") resolve(event);
          newline = buffer.indexOf("\n");
        }
      });
      child.on("error", reject);
      child.on("exit", () => reject(new Error(`worker exited early: ${JSON.stringify(events)}`)));
    });
    child.stdin.write(`${bootstrapLine(base, executable, major)}\n`);
    // The ready line carries the health port; the render command follows it.
    await new Promise((resolve) => setTimeout(resolve, 250));
    child.stdin.write(`${sandboxCommandLine(sandbox)}\n`);
    const event = await settled;
    // The frames themselves, hashed before the workspace goes away. Without
    // them a render can only be judged by its event, and the defect this file
    // exists to catch is precisely one that reports a perfect event.
    const frames = [];
    try {
      for (const name of (await readdir(join(workspace, "frames"))).sort()) {
        frames.push(createHash("sha256").update(await readFile(join(workspace, "frames", name))).digest("hex"));
      }
    } catch {
      // A failed render leaves no frames directory; the event says so.
    }
    return { ...event, frameDigests: frames };
  } finally {
    child.kill("SIGKILL");
    await rm(base, { recursive: true, force: true });
  }
}

test("a composition whose animation runtime never loads fails instead of shipping a still image", async (t) => {
  const executable = locateRenderBrowser();
  if (executable === null) {
    t.skip("no staged embedded Chromium on this machine");
    return;
  }
  const major = await chromiumMajor(executable);
  const event = await renderPage(FROZEN_PAGE, executable, major);
  assert.equal(
    event.reasonCode,
    "render_static_frames",
    `a render where every frame is identical must be reported as a failure, got ${JSON.stringify(event)}`,
  );
  assert.equal(event.event, "worker.render.failed");
});

test("a composition that actually animates still renders", async (t) => {
  const executable = locateRenderBrowser();
  if (executable === null) {
    t.skip("no staged embedded Chromium on this machine");
    return;
  }
  const major = await chromiumMajor(executable);
  const event = await renderPage(MOVING_PAGE, executable, major);
  assert.equal(
    event.event,
    "worker.render.sandboxed",
    `a moving composition must not be mistaken for a still one, got ${JSON.stringify(event)}`,
  );
  assert.equal(event.framesCaptured, FRAME_COUNT);
});

// T92 moved the document from model output to `composition_template.py`. That
// makes the still-frame shape above this project's own liability rather than the
// model's, so the template's real output is put through the same real Worker and
// real Chromium: the deterministic Python tests can only prove the document
// passes the *static* gates, and a document that passes all of them while never
// repainting is exactly the defect T86 shipped.
const PYTHON = fileURLToPath(new URL("backend/.venv/bin/python", repositoryRoot));
const TEMPLATE_DURATION = 3;
const RENDER_TEMPLATE = `
import sys
sys.path.insert(0, "backend/src")
from automation_tool.executor.motion_authoring.composition_template import (
    AUTHORING_RUNTIME_ASSET, TemplateScene, render_composition,
)
scenes = (
    TemplateScene("clip-1", "title", "本周销售增长", "三个要点", (), 0.0, 1.5),
    TemplateScene("clip-2", "points", "转化提升", "渠道与私域", ("投放", "承接"), 1.5, 1.5),
)
sys.stdout.write(render_composition(
    primary_color="#1e3a8a", secondary_color="#38bdf8", scenes=scenes,
    duration_seconds=${TEMPLATE_DURATION}, stage_width=640, stage_height=360,
    runtime_asset=AUTHORING_RUNTIME_ASSET,
))
`;

test("the local composition template renders a film that actually moves", async (t) => {
  const executable = locateRenderBrowser();
  if (executable === null) {
    t.skip("no staged embedded Chromium on this machine");
    return;
  }
  const runtime = locateAnimationRuntime();
  if (runtime === null) {
    t.skip("no staged animation runtime on this machine");
    return;
  }
  if (!existsSync(PYTHON)) {
    t.skip("no backend virtual environment on this machine");
    return;
  }
  const { stdout: html } = await execFileAsync(PYTHON, ["-c", RENDER_TEMPLATE], {
    cwd: fileURLToPath(repositoryRoot),
    maxBuffer: 4_000_000,
  });
  assert.ok(html.includes(`src="${RUNTIME_ASSET}"`), "the template must load the local runtime");
  const major = await chromiumMajor(executable);
  const event = await renderPage(html, executable, major, [
    { relative: RUNTIME_ASSET, source: runtime },
  ]);
  assert.equal(
    event.event,
    "worker.render.sandboxed",
    `the shipped template must render as a moving film, got ${JSON.stringify(event)}`,
  );
  assert.equal(event.framesCaptured, FRAME_COUNT);
  assert.equal(event.blockedRequests, 0, "the template must not ask the offline sandbox for anything");
});

/**
 * 两个镜头共用一份文档时，各自只渲染自己那一截。
 *
 * 这是 2026-07-28 那条留档成片的成因，也是这个文件唯一抓不到的一类失败：
 * 段只带「哪个页面、多少帧」的时候，Worker 只剩一条规则可用——把页面整条时间轴
 * 摊到这些帧上。对一次拍完的片子是对的，对镜头共用一份文档的片子就是错的：
 * 每个模板镜头都把整部片子重渲了一遍。那条 12 秒成片是两段一模一样的 6 秒，
 * 各自倍速；编码、画布、帧数、时长、静帧门禁**全绿**。
 *
 * 所以这条比对的是帧本身。事件说不出这件事。
 */
test("two shots of one document render different stretches of it", async (t) => {
  const executable = locateRenderBrowser();
  if (executable === null) {
    t.skip("no staged embedded Chromium on this machine");
    return;
  }
  const major = await chromiumMajor(executable);
  const first = await renderPage(MOVING_PAGE, executable, major, [], { start: 0, end: 1.5 });
  const second = await renderPage(MOVING_PAGE, executable, major, [], { start: 1.5, end: 3 });

  assert.equal(first.event, "worker.render.sandboxed", JSON.stringify(first));
  assert.equal(second.event, "worker.render.sandboxed", JSON.stringify(second));
  assert.equal(first.frameDigests.length, FRAME_COUNT);
  assert.equal(second.frameDigests.length, FRAME_COUNT);
  assert.notDeepEqual(
    first.frameDigests,
    second.frameDigests,
    "两段各自渲染同一份文档的不同时间段，画面不可能逐帧相同——相同就说明窗口没生效",
  );
});
