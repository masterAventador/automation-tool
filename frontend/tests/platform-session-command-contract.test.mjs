import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

const SOURCES = {
  rust: "frontend/src-tauri/src/executor_bootstrap.rs",
  gateway: "frontend/src/features/platform-sessions/platform-session-gateway.ts",
};

async function readSources() {
  const entries = await Promise.all(
    Object.entries(SOURCES).map(async ([name, relative]) => [
      name,
      await readFile(new URL(relative, repositoryRoot), "utf8"),
    ]),
  );
  return Object.fromEntries(entries);
}

function camelCase(field) {
  return field.replace(/_([a-z])/gu, (_match, letter) => letter.toUpperCase());
}

/** The serialized field names of one `camelCase` Rust struct, in declaration order. */
function rustSerializedFields(source, structName) {
  const start = source.indexOf(`pub struct ${structName} {`);
  assert.ok(start >= 0, `missing Rust struct ${structName}`);
  const attributes = source.slice(Math.max(0, start - 200), start);
  assert.match(
    attributes,
    /#\[serde\(rename_all = "camelCase"\)\]/u,
    `${structName} must serialize camelCase for the gateway to mirror it`,
  );
  const body = source.slice(start, source.indexOf("\n}", start));
  return [...body.matchAll(/^ {4}(?:pub(?:\(crate\))? )?([a-z][a-z0-9_]*)\s*:/gmu)].map((match) =>
    camelCase(match[1]),
  );
}

/** The top-level keys of one strict `z.object({...})` declaration, in order. */
function zodObjectKeys(source, constantName) {
  const start = source.indexOf(`const ${constantName} = z`);
  assert.ok(start >= 0, `missing Zod schema ${constantName}`);
  const end = source.indexOf(".strict()", start);
  assert.ok(
    end > start,
    `${constantName} must stay .strict(); a permissive schema hides producer drift`,
  );
  const body = source.slice(start, end);
  return [...body.matchAll(/^ {4}([A-Za-z][A-Za-z0-9]*):\s/gmu)].map((match) => match[1]);
}

test("the local platform command result has one field set in Rust and the gateway", async () => {
  const sources = await readSources();
  // `open_douyin_login` and `recheck_douyin_login` return this struct verbatim, so
  // a producer field the strict gateway schema does not declare turns every real
  // "打开登录处理" click into `protocol_mismatch` - the Command succeeds, the
  // browser reports a real page fact, and the operator still sees only
  // "暂时无法读取抖音登录状态". Nothing about the Rust or React side alone is wrong,
  // which is why this has to be asserted across the seam.
  assert.deepEqual(
    zodObjectKeys(sources.gateway, "platformSessionActionSchema"),
    rustSerializedFields(sources.rust, "LocalPlatformCommandResult"),
    "platformSessionActionSchema drifted from LocalPlatformCommandResult",
  );
});
