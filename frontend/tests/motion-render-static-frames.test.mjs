// A render that never moves must fail loudly instead of shipping a still image.
//
// The original defect: the packaged authoring reference
// (`vendor/hyperframes/.../minimal-composition.md`) demonstrates the animation
// runtime as a CDN `<script src="https://...gsap...">`. The render sandbox is
// offline by construction, so that tag resolves to nothing, `gsap` stays
// undefined, and the inline setup throws before it can register
// `window.__timelines`. The Worker must now reject that declared-but-missing
// timeline after bounded warm-up as a static render instead of capturing an
// untouched first paint and reporting `status: "complete"`.
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
import { dirname, join } from "node:path";
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
  const explicit = Number(process.env.AUTOMATION_TOOL_RENDER_BROWSER_MAJOR);
  if (Number.isInteger(explicit) && explicit > 0) return explicit;
  if (process.platform === "win32") {
    const manifest = (await readdir(dirname(executable))).find((name) =>
      /^\d+\.\d+\.\d+\.\d+\.manifest$/u.test(name)
    );
    const major = Number(manifest?.split(".", 1)[0]);
    assert.ok(Number.isInteger(major), `could not read Chromium major beside ${executable}`);
    return major;
  }
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
const RELEASE_CATALOG = fileURLToPath(
  new URL(".local/motion-catalog-release/1.0.0", repositoryRoot),
);

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

async function renderPage(
  html,
  executable,
  major,
  assets = [],
  window = null,
  entryHtml = "entry.html",
) {
  const base = await mkdtemp(join(tmpdir(), "t86-static-"));
  const workspace = join(base, "workspace");
  await mkdir(workspace, { recursive: true });
  const entry = join(workspace, entryHtml);
  await mkdir(join(entry, ".."), { recursive: true });
  await writeFile(entry, html, "utf8");
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
    entryHtml,
    frameCount: FRAME_COUNT,
    // Which stretch of the entry's own timeline this render covers. Default is
    // the whole of it, which is what a film captured in one pass asks for.
    sourceStartMillis: Math.round((window?.start ?? 0) * 1000),
    sourceEndMillis: Math.round((window?.end ?? TEMPLATE_DURATION) * 1000),
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
    // A failed render leaves no frames directory, and that has to stay
    // distinguishable from a successful render of zero frames: swallowing the
    // error into an empty list is how `assert.notDeepEqual([], [])` would pass
    // over two renders that never happened. `null` cannot be compared by
    // accident.
    let frames = null;
    try {
      const names = (await readdir(join(workspace, "frames"))).sort();
      frames = [];
      for (const name of names) {
        frames.push(createHash("sha256").update(await readFile(join(workspace, "frames", name))).digest("hex"));
      }
    } catch {
      frames = null;
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
    `a document that declares but never registers its timeline must be rejected as static, got ${JSON.stringify(event)}`,
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
const PYTHON = process.env.AUTOMATION_TOOL_TEST_PYTHON
  ?? ["backend/.venv/bin/python", "backend/.venv/Scripts/python.exe"]
    .map((candidate) => fileURLToPath(new URL(candidate, repositoryRoot)))
    .find((candidate) => existsSync(candidate))
  ?? "python3";
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

const RENDER_FRAGMENT_COMPONENT = `
import sys
sys.path.insert(0, "backend/src")
from pathlib import Path
from automation_tool.executor.motion_authoring.component_host import build_component_film_html
name = sys.argv[1]
source = Path(f"vendor/hyperframes/registry/components/{name}/{name}.html").read_text(encoding="utf-8")
sys.stdout.write(build_component_film_html(
    name=name, source=source, headline="本周销售增长",
    body="方向性拖影", items=(),
))
`;

const RENDER_STATIC_OVERLAY_WORKING_COPY = `
import sys
sys.path.insert(0, "backend/src")
from pathlib import Path
from automation_tool.executor.motion_authoring.part_workspace import write_part_working_copy
class Workspace:
    def __init__(self): self.text = {}
    def write_text(self, path, value): self.text[path] = value
    def write_bytes(self, path, value): pass
workspace = Workspace()
entry = write_part_working_copy(
    workspace=workspace, catalog_root=Path(sys.argv[1]), name="lower-third-bild",
    slots=(), copy={}, font_css="",
)
sys.stdout.write(workspace.text[entry])
`;

test("the static overlay becomes a moving standalone film shot", async (t) => {
  const executable = locateRenderBrowser();
  const runtime = locateAnimationRuntime();
  const font = join(
    RELEASE_CATALOG,
    "items/lower-third-bild/assets/fonts/big-shoulders-display-latin.woff2",
  );
  if (executable === null || runtime === null || !existsSync(font)) {
    t.skip("the staged Chromium or release catalog is unavailable");
    return;
  }
  const { stdout: html } = await execFileAsync(
    PYTHON,
    ["-c", RENDER_STATIC_OVERLAY_WORKING_COPY, RELEASE_CATALOG],
    { cwd: fileURLToPath(repositoryRoot), maxBuffer: 4_000_000 },
  );
  const event = await renderPage(
    html,
    executable,
    await chromiumMajor(executable),
    [
      {
        relative: "catalog/offline-deps/js/gsap-3.14.2/gsap.min.js",
        source: runtime,
      },
      {
        relative: "catalog/items/lower-third-bild/assets/fonts/big-shoulders-display-latin.woff2",
        source: font,
      },
    ],
    { start: 0, end: 8 },
    "catalog/items/lower-third-bild/lower-third-bild.html",
  );
  assert.equal(
    event.event,
    "worker.render.sandboxed",
    `the selectable overlay must animate, got ${JSON.stringify(event)}`,
  );
});

test("every production fragment component host renders a moving offline shot", async (t) => {
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
  const major = await chromiumMajor(executable);
  const names = [
    "caption-blend-difference",
    "grain-overlay",
    "grid-pixelate-wipe",
    "motion-blur",
    "parallax-unzoom",
    "parallax-zoom",
    "shimmer-sweep",
    "texture-mask-text",
    "vignette",
  ];
  for (const name of names) {
    const { stdout: html } = await execFileAsync(
      PYTHON,
      ["-c", RENDER_FRAGMENT_COMPONENT, name],
      { cwd: fileURLToPath(repositoryRoot), maxBuffer: 4_000_000 },
    );
    const event = await renderPage(
      html,
      executable,
      major,
      [{
        relative: "catalog/offline-deps/js/gsap-3.14.2/gsap.min.js",
        source: runtime,
      }],
      { start: 0, end: 3 },
      `catalog/items/${name}/${name}.html`,
    );
    assert.equal(
      event.event,
      "worker.render.sandboxed",
      `${name} must animate, got ${JSON.stringify(event)}`,
    );
  }
});

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
 * 窗口越过文档时间轴时拒绝，而不是悄悄收窄。
 *
 * 完全越界还好办——所有帧落在同一点，静帧门禁会响。**部分越界才是阴的**：
 * [5s, 9s] 对一份 6 秒的文档，前四分之一的帧在动、后四分之三全停在 6 秒处；
 * `sawMovement` 被前面那几帧置上，帧数对，渲染报 complete，
 * 而这个镜头四分之三是静止画面，下游每一道门禁都是绿的。
 *
 * 收窄是「下游给个合理默认值」——这条线这周被这个模式咬了六次。
 * Worker 是唯一知道 `seekableDuration` 的一层，所以只能它说不。
 */
test("a window the document cannot satisfy is refused, not narrowed", async (t) => {
  const executable = locateRenderBrowser();
  if (executable === null) {
    t.skip("no staged embedded Chromium on this machine");
    return;
  }
  const major = await chromiumMajor(executable);
  // 文档声明 3 秒，窗口要到 5 秒。
  const event = await renderPage(MOVING_PAGE, executable, major, [], { start: 2, end: 5 });

  assert.equal(event.event, "worker.render.failed", JSON.stringify(event));
  assert.equal(event.reasonCode, "render_window_outside_timeline", JSON.stringify(event));
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
  assert.equal(first.frameDigests?.length, FRAME_COUNT, "第一段必须真的截出帧来");
  assert.equal(second.frameDigests?.length, FRAME_COUNT, "第二段必须真的截出帧来");
  assert.notDeepEqual(
    first.frameDigests,
    second.frameDigests,
    "两段各自渲染同一份文档的不同时间段，画面不可能逐帧相同——相同就说明窗口没生效",
  );
});
