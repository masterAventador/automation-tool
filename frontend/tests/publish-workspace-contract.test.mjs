import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

const SOURCES = {
  contract: "contracts/publishing/publish-workspace.v1.json",
  capabilities: "contracts/quality/publishing-capabilities.v1.json",
  rust: "frontend/src-tauri/src/publish_workspace.rs",
  gateway: "frontend/src/features/publishing/publish-workspace-gateway.ts",
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

/** Every quoted string of one Rust enum body, in declaration order. */
function rustSerdeVariants(source, enumName) {
  const start = source.indexOf(`pub enum ${enumName} {`);
  assert.ok(start >= 0, `missing Rust enum ${enumName}`);
  const body = source.slice(start, source.indexOf("\n}", start));
  return [...body.matchAll(/rename\s*=\s*"([a-z_]+)"/gu)].map((match) => match[1]);
}

/** Every member of one `z.enum([...])` literal in the gateway. */
function zodEnumMembers(source, constantName) {
  const declaration = new RegExp(
    `const ${constantName}\\s*=\\s*z\\.enum\\(\\[([^\\]]*)\\]\\)`,
    "u",
  ).exec(source);
  assert.ok(declaration, `missing Zod enum ${constantName}`);
  return [...declaration[1].matchAll(/"([a-z_]+)"/gu)].map((match) => match[1]);
}

test("the publish workspace vocabulary has one definition in every language", async () => {
  const sources = await readSources();
  const contract = JSON.parse(sources.contract);

  assert.equal(contract.version, "publish-workspace.v1");

  for (const [name, rustEnum, zodEnum] of [
    ["platforms", "PublishPlatform", "publishPlatform"],
    ["availability", "PublishAvailability", "publishAvailability"],
    ["stages", "PublishStage", "publishStage"],
    ["outcomes", "PublishOutcome", "publishOutcome"],
  ]) {
    const declared = contract[name];
    assert.ok(Array.isArray(declared) && declared.length > 0, `contract.${name} must be a list`);
    assert.deepEqual(
      rustSerdeVariants(sources.rust, rustEnum),
      declared,
      `Rust ${rustEnum} drifted from contract.${name}`,
    );
    assert.deepEqual(
      zodEnumMembers(sources.gateway, zodEnum),
      declared,
      `Zod ${zodEnum} drifted from contract.${name}`,
    );
  }
});

test("every audit step the Rust workspace can record is declared", async () => {
  const sources = await readSources();
  const contract = JSON.parse(sources.contract);

  const recorded = [...sources.rust.matchAll(/self\.record\(\s*"([a-z_]+)"/gu)].map(
    (match) => match[1],
  );
  assert.ok(recorded.length > 0, "the Rust workspace records no audit step at all");
  assert.deepEqual([...new Set(recorded)].sort(), [...contract.auditSteps].sort());
  assert.deepEqual(zodEnumMembers(sources.gateway, "publishAuditStep"), contract.auditSteps);
});

test("only the two platforms the capabilities contract enables are publishable", async () => {
  const sources = await readSources();
  const contract = JSON.parse(sources.contract);
  const capabilities = JSON.parse(sources.capabilities);

  const enabled = Object.entries(capabilities.platforms)
    .filter(([, platform]) => platform.enabled)
    .map(([name]) => name)
    .sort();

  assert.deepEqual([...contract.platforms].sort(), enabled);
});

test("the publish workspace never names an upstream technology to the user", async () => {
  const sources = await readSources();
  const contract = JSON.parse(sources.contract);

  const forbidden = ["browser_use", "browser use", "playwright", "chromium", "official_api"];
  const userFacing = JSON.stringify(contract.userFacing);
  for (const term of forbidden) {
    assert.ok(
      !userFacing.toLowerCase().includes(term),
      `user-facing copy must not name ${term}`,
    );
  }
  // The mechanism is an internal fact; it must not reach the projected DTO at all.
  assert.equal(contract.projectsMechanismToTheApp, false);
});

test("every stage and outcome the App can show has user-facing copy", async () => {
  const sources = await readSources();
  const contract = JSON.parse(sources.contract);

  for (const stage of contract.stages) {
    assert.ok(contract.userFacing.stages[stage], `stage ${stage} has no copy`);
  }
  for (const outcome of contract.outcomes) {
    assert.ok(contract.userFacing.outcomes[outcome], `outcome ${outcome} has no copy`);
  }
  for (const availability of contract.availability) {
    assert.ok(
      contract.userFacing.availability[availability],
      `availability ${availability} has no copy`,
    );
  }
});
