import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const TAURI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../src-tauri");
const PRODUCTION_CONFIG = "tauri.conf.json";

/**
 * Configurations that deliberately run against the production data directory.
 *
 * `dev` is the developer's own app — using the real directory is the point.
 * The two candidate configurations exist to produce something as close to a
 * shipped package as possible, so they must carry the shipped identifier.
 */
const INTENTIONALLY_PRODUCTION = new Set([
  "tauri.dev.conf.json",
  "tauri.macos-candidate.conf.json",
  "tauri.windows-candidate.conf.json",
]);

async function productionIdentifier() {
  const document = JSON.parse(
    await readFile(join(TAURI_ROOT, PRODUCTION_CONFIG), "utf8"),
  );
  assert.ok(document.identifier, "the production configuration has no identifier");
  return document.identifier;
}

test("every test configuration writes to its own app data directory", async () => {
  // `tauri.test.conf.json` had no identifier and therefore inherited the
  // production one, so `pnpm test:tauri` wrote into the live data directory —
  // the one holding platform login sessions a user had to scan a QR code for.
  // The other 41 e2e configurations all isolate themselves; this one was the
  // single hole, and nothing would have reported it.
  const identifier = await productionIdentifier();
  const configurations = (await readdir(TAURI_ROOT)).filter(
    (name) =>
      name.startsWith("tauri.") &&
      name.endsWith(".conf.json") &&
      name !== PRODUCTION_CONFIG,
  );
  assert.ok(configurations.length > 0, "no Tauri configurations were found");

  const offenders = [];
  for (const name of configurations) {
    if (INTENTIONALLY_PRODUCTION.has(name)) {
      continue;
    }
    const document = JSON.parse(await readFile(join(TAURI_ROOT, name), "utf8"));
    if (document.identifier === undefined || document.identifier === identifier) {
      offenders.push(name);
    }
  }
  assert.deepEqual(offenders, [], "configurations sharing the production identifier");
});

test("the production-directory exemption list stays honest", async () => {
  // A configuration that no longer exists must not keep its exemption, or the
  // list silently becomes a place to hide a new one.
  const present = new Set(await readdir(TAURI_ROOT));
  const stale = [...INTENTIONALLY_PRODUCTION].filter((name) => !present.has(name));
  assert.deepEqual(stale, [], "exempted configurations that no longer exist");
});
