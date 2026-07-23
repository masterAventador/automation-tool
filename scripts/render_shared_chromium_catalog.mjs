#!/usr/bin/env node

import { createHash } from "node:crypto";
import {
  cpSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  captureFrame,
  closeCaptureSession,
  createCaptureSession,
  createFileServer,
  getCompositionDuration,
  initializeSession,
} from "../tools/shared-browser-validation/node_modules/@hyperframes/producer/dist/index.js";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDir, "..");
const registryRoot = join(repositoryRoot, "vendor", "hyperframes", "registry");
const gsapPath = resolve(
  repositoryRoot,
  "tools",
  "shared-browser-validation",
  "node_modules",
  "gsap",
  "dist",
  "gsap.min.js",
);

function parseArgs() {
  const values = new Map();
  for (let index = 2; index < process.argv.length; index += 2) {
    values.set(process.argv[index], process.argv[index + 1]);
  }
  const browserPath = values.get("--browser-path");
  const output = values.get("--output");
  const rawLimit = values.get("--limit");
  const rawStart = values.get("--start");
  if (!browserPath || !output) {
    throw new Error("usage: render_shared_chromium_catalog.mjs --browser-path PATH --output JSON");
  }
  const limit = rawLimit === undefined ? null : Number(rawLimit);
  const start = rawStart === undefined ? 0 : Number(rawStart);
  if (limit !== null && (!Number.isInteger(limit) || limit < 1 || limit > 134)) {
    throw new Error("--limit must be an integer from 1 to 134");
  }
  if (!Number.isInteger(start) || start < 0 || start > 133) {
    throw new Error("--start must be an integer from 0 to 133");
  }
  return { browserPath: resolve(browserPath), output: resolve(output), limit, start };
}

function discoverItems() {
  const items = [];
  for (const kind of ["blocks", "components"]) {
    const kindRoot = join(registryRoot, kind);
    for (const entry of readdirSync(kindRoot, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const sourceDir = join(kindRoot, entry.name);
      const manifest = JSON.parse(readFileSync(join(sourceDir, "registry-item.json"), "utf8"));
      let entryFile = "demo.html";
      let entryTarget = entryFile;
      if (kind === "blocks") {
        const composition = manifest.files.find(
          (file) => file.type === "hyperframes:composition",
        );
        entryFile = composition?.path ?? `${entry.name}.html`;
        entryTarget = composition?.target ?? entryFile;
      }
      if (!existsSync(join(sourceDir, entryFile))) {
        throw new Error(`catalog entry file missing: ${kind}/${entry.name}/${entryFile}`);
      }
      items.push({ name: entry.name, kind, sourceDir, entryFile, entryTarget, manifest });
    }
  }
  items.sort((left, right) => `${left.kind}/${left.name}`.localeCompare(`${right.kind}/${right.name}`));
  return items;
}

function localizeGsap(projectDir) {
  cpSync(gsapPath, join(projectDir, "gsap.min.js"));
  for (const entry of readdirSync(projectDir, { recursive: true, withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".html")) continue;
    const path = join(entry.parentPath ?? entry.path, entry.name);
    const html = readFileSync(path, "utf8").replaceAll(
      "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js",
      "/gsap.min.js",
    );
    writeFileSync(path, html, "utf8");
  }
}

function prepareCatalogProject(item) {
  const projectDir = mkdtempSync(join(tmpdir(), `automation-tool-eb02-${item.name}-`));
  cpSync(item.sourceDir, projectDir, { recursive: true });
  for (const file of item.manifest.files ?? []) {
    const source = join(item.sourceDir, file.path);
    const target = join(projectDir, file.target);
    mkdirSync(dirname(target), { recursive: true });
    cpSync(source, target, { recursive: true });
  }
  const entryPath = join(projectDir, item.entryFile);
  let entryHtml = readFileSync(entryPath, "utf8");
  if (!entryHtml.includes("data-duration=")) {
    const fallbackDuration = item.manifest.duration ?? 5;
    entryHtml = entryHtml.replace(
      /data-composition-id=(["'][^"']+["'])/,
      `data-composition-id=$1 data-duration="${fallbackDuration}"`,
    );
  }
  if (item.kind === "components" && entryHtml.includes("__timelines")) {
    writeFileSync(join(projectDir, "index.html"), entryHtml, "utf8");
  } else {
    const width = item.manifest.dimensions?.width ?? 1920;
    const height = item.manifest.dimensions?.height ?? 1080;
    const duration = item.manifest.duration ?? 5;
    const nestedId =
      entryHtml.match(/data-composition-id=["']([^"']+)["']/)?.[1] ?? item.name;
    writeFileSync(
      join(projectDir, "index.html"),
      `<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=${width}, height=${height}"><script src="/gsap.min.js"></script><style>*{box-sizing:border-box}html,body{margin:0;width:${width}px;height:${height}px;overflow:hidden;background:#111}</style></head><body><div data-composition-id="root" data-width="${width}" data-height="${height}" data-start="0" data-duration="${duration}"><div data-composition-id="${nestedId}" data-composition-src="${item.entryTarget}" data-no-timeline data-start="0" data-duration="${duration}" data-width="${width}" data-height="${height}"></div></div><script>window.__timelines=window.__timelines||{};window.__timelines.root=gsap.timeline({paused:true});</script></body></html>`,
      "utf8",
    );
  }
  localizeGsap(projectDir);
  return projectDir;
}

function styleHtml(style, index) {
  const palettes = [
    ["#f4ecd8", "#f1df35", "#222a80"],
    ["#fff8e7", "#ff71ce", "#111111"],
    ["#f2eee3", "#1955d1", "#15213d"],
    ["#fff9eb", "#df2020", "#1f1714"],
    ["#f15a24", "#191919", "#f7edda"],
    ["#f7efe3", "#ff6b5a", "#111111"],
    ["#eee8dc", "#9d8d7a", "#181713"],
    ["#eee9dc", "#1748b5", "#191919"],
    ["#fff4e9", "#ff6f61", "#26262b"],
    ["#fff2dd", "#ff42aa", "#181818"],
    ["#fff4a8", "#9cd8c8", "#292929"],
    ["#f5ead8", "#386641", "#d9668b"],
  ];
  const [background, accent, ink] = palettes[index];
  return `<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=1920, height=1080"><script src="/gsap.min.js"></script><style>*{box-sizing:border-box}html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:${background};color:${ink};font-family:Arial,sans-serif}.frame{position:relative;width:100%;height:100%;padding:100px;border:12px solid ${ink}}.accent{position:absolute;width:620px;height:620px;border-radius:50%;right:90px;top:90px;background:${accent};filter:blur(2px)}h1{position:relative;margin:260px 0 0;font-size:132px;line-height:.9;max-width:1200px}.rule{position:absolute;left:100px;right:100px;bottom:100px;height:4px;background:${ink}}</style></head><body><div class="frame" data-composition-id="main" data-width="1920" data-height="1080" data-start="0" data-duration="1"><div class="accent"></div><h1>${style}</h1><div class="rule"></div></div><script>window.__timelines=window.__timelines||{};const tl=gsap.timeline({paused:true});tl.from("h1",{opacity:0,y:80,duration:.7},0);tl.from(".accent",{scale:.3,opacity:0,duration:.8},0);window.__timelines.main=tl;</script></body></html>`;
}

function prepareStyleProject(style, index) {
  const projectDir = mkdtempSync(join(tmpdir(), `automation-tool-eb02-style-${index}-`));
  writeFileSync(join(projectDir, "index.html"), styleHtml(style, index), "utf8");
  cpSync(gsapPath, join(projectDir, "gsap.min.js"));
  return projectDir;
}

function readDimensions(html, manifest) {
  const width = Number(html.match(/data-width=["'](\d+)/)?.[1] ?? manifest?.dimensions?.width ?? 1920);
  const height = Number(html.match(/data-height=["'](\d+)/)?.[1] ?? manifest?.dimensions?.height ?? 1080);
  return { width, height };
}

async function captureOne(projectDir, manifest, browserPath) {
  const html = readFileSync(join(projectDir, "index.html"), "utf8");
  const { width, height } = readDimensions(html, manifest);
  const framesDir = join(projectDir, "_frames");
  mkdirSync(framesDir, { recursive: true });
  const server = await createFileServer({ projectDir, port: 0, fps: { num: 30, den: 1 } });
  let session;
  try {
    session = await createCaptureSession(
      server.url,
      framesDir,
      { width, height, fps: { num: 30, den: 1 }, format: "png" },
      null,
      {
        chromePath: browserPath,
        forceScreenshot: true,
        browserGpuMode: "software",
        enableBrowserPool: true,
      },
    );
    await initializeSession(session);
    const duration = await getCompositionDuration(session).catch(() => 1);
    const result = await captureFrame(session, 0, Math.min(3, Math.max(0, duration * 0.6)));
    const bytes = readFileSync(result.path);
    if (bytes.length < 100 || bytes.subarray(1, 4).toString("ascii") !== "PNG") {
      throw new Error(`invalid PNG capture (${bytes.length} bytes)`);
    }
    return {
      sha256: createHash("sha256").update(bytes).digest("hex"),
      bytes: bytes.length,
      width,
      height,
      capture_mode: session.captureMode,
    };
  } finally {
    if (session) await closeCaptureSession(session).catch(() => {});
    server.close();
  }
}

async function main() {
  const { browserPath, output, limit, start } = parseArgs();
  if (!existsSync(browserPath)) throw new Error(`browser not found: ${browserPath}`);
  if (!existsSync(gsapPath)) throw new Error(`local GSAP runtime not found: ${gsapPath}`);

  const catalog = [];
  const discoveredItems = discoverItems();
  const items =
    limit === null ? discoveredItems.slice(start) : discoveredItems.slice(start, start + limit);
  if (discoveredItems.length !== 134) {
    throw new Error(`expected 134 installable items, found ${discoveredItems.length}`);
  }
  for (const [index, item] of items.entries()) {
    const projectDir = prepareCatalogProject(item);
    try {
      const result = await captureOne(projectDir, item.manifest, browserPath);
      catalog.push({ name: item.name, kind: item.kind, ...result });
      process.stdout.write(`catalog ${index + 1}/${items.length} ${item.kind}/${item.name}\n`);
    } finally {
      rmSync(projectDir, { recursive: true, force: true });
    }
  }

  const styles = JSON.parse(
    readFileSync(
      join(repositoryRoot, "contracts", "browser", "shared-chromium-validation.v1.json"),
      "utf8",
    ),
  ).styles;
  const styleResults = [];
  for (const [index, style] of styles.entries()) {
    const projectDir = prepareStyleProject(style, index);
    try {
      styleResults.push({ name: style, ...(await captureOne(projectDir, null, browserPath)) });
    } finally {
      rmSync(projectDir, { recursive: true, force: true });
    }
  }

  const result = {
    renderer: "@hyperframes/producer",
    renderer_version: "0.7.68",
    browser_path_basename: basename(browserPath),
    catalog,
    catalog_inventory_count: discoveredItems.length,
    partial: limit !== null || start !== 0,
    start,
    styles: styleResults,
  };
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify(result, null, 2)}\n`, "utf8");
  console.log(`render validation complete: ${catalog.length} catalog items, ${styleResults.length} styles`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
