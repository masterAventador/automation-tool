import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);

async function readProjectFile(path) {
  return readFile(new URL(path, frontendRoot), "utf8");
}

test("desktop frontend owns one private pnpm package with the required stack", async () => {
  const packageJson = JSON.parse(await readProjectFile("package.json"));

  assert.equal(packageJson.name, "automation-tool-desktop");
  assert.equal(packageJson.private, true);
  assert.match(packageJson.packageManager, /^pnpm@\d+\.\d+\.\d+$/);
  assert.deepEqual(Object.keys(packageJson.dependencies).sort(), [
    "@tanstack/react-query",
    "@tauri-apps/api",
    "antd",
    "react",
    "react-dom",
    "zod",
  ]);

  for (const dependency of ["@vitejs/plugin-react", "typescript", "vite"]) {
    assert.equal(typeof packageJson.devDependencies[dependency], "string");
  }

  for (const script of ["build", "dev", "lint", "test", "typecheck"]) {
    assert.equal(typeof packageJson.scripts[script], "string");
  }
});

test("Vite is a desktop asset builder without a web deployment entry", async () => {
  const packageJson = JSON.parse(await readProjectFile("package.json"));
  const forbiddenScripts = ["deploy", "publish", "serve"];

  assert.equal(packageJson.homepage, undefined);
  for (const script of forbiddenScripts) {
    assert.equal(packageJson.scripts[script], undefined);
  }

  for (const path of ["vercel.json", "netlify.toml", "firebase.json", "Dockerfile"]) {
    await assert.rejects(stat(new URL(path, frontendRoot)), { code: "ENOENT" });
  }
});

test("strict TypeScript React entry and Vite configuration are present", async () => {
  const [html, main, app, tsconfig, viteConfig] = await Promise.all([
    readProjectFile("index.html"),
    readProjectFile("src/main.tsx"),
    readProjectFile("src/app/App.tsx"),
    readProjectFile("tsconfig.app.json"),
    readProjectFile("vite.config.ts"),
  ]);

  assert.match(html, /<div id="root"><\/div>/);
  assert.match(main, /createRoot/);
  assert.match(app, /from "antd"/);
  assert.equal(JSON.parse(tsconfig).compilerOptions.strict, true);
  assert.match(viteConfig, /host: "127\.0\.0\.1"/);
  assert.match(viteConfig, /strictPort: true/);
});
