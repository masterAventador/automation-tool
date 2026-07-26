import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

const SOURCES = {
  agent: "backend/src/automation_tool/executor/motion_authoring/agent.py",
  contract: "contracts/video/motion-render-cancel-marker.v1.json",
  orchestrator: "frontend/src-tauri/src/local_video_orchestrator.rs",
  studio: "frontend/src-tauri/src/motion_video_studio.rs",
  worker: "workers/motion_composition/worker.mjs",
};

const read = (name) => readFile(new URL(SOURCES[name], repositoryRoot), "utf8");

const contract = async () => JSON.parse(await read("contract"));

/**
 * The name of the cancellation marker is one value, and it lives here.
 *
 * It used to be a literal on both sides of the process boundary. Nothing
 * connected them, so changing one was a silent change: the button still
 * answers, the job still reads 已取消, and only the render keeps going.
 */
test("the cancellation marker is declared once and is a plain workspace file name", async () => {
  const declared = await contract();
  assert.equal(declared.version, "motion-render-cancel-marker.v1");
  assert.equal(declared.policy, "fail_closed");

  const name = declared.markerFileName;
  assert.equal(typeof name, "string", "the contract must declare the marker file name");
  assert.ok(name.length > 0, "an empty marker name would match nothing");
  assert.doesNotMatch(
    name,
    /[/\\]|^\.{1,2}$|\0/u,
    "the marker is a single file inside the RenderJob workspace, never a path",
  );
});

/**
 * The Worker is handed the name; it does not know it.
 *
 * This is the property that makes the two sides unable to disagree. A second
 * declaration read from a second file would still be two things kept equal by
 * a test; one value that travels is one thing.
 */
test("the render Worker holds no marker name of its own", async () => {
  const [worker, declared] = await Promise.all([read("worker"), contract()]);

  assert.doesNotMatch(
    worker,
    new RegExp(escapeForRegExp(declared.markerFileName), "u"),
    "the Worker must not restate the marker name",
  );
  assert.doesNotMatch(
    worker,
    /SANDBOX_CANCEL_FILE/u,
    "the Worker's own marker constant is the second source this contract removes",
  );
  assert.match(
    worker,
    /spec\.cancelMarker/u,
    "the Worker must watch the marker the App named in the render request",
  );
});

/**
 * A Worker that does not understand the field refuses the render.
 *
 * The spec is checked with an exact key set, so `cancelMarker` being listed
 * there is what turns "this Worker cannot be cancelled" into "this Worker
 * will not start". The name is validated like every other workspace-relative
 * path the spec carries, so it cannot point outside the RenderJob.
 */
test("a render whose spec omits or escapes the marker is refused", async () => {
  const worker = await read("worker");

  const keySet = worker.match(
    /function validSandboxSpec\(value\) \{\s*if \(!hasExactKeys\(value, \[([\s\S]*?)\]\)\)/u,
  );
  assert.ok(keySet, "the sandbox spec must still be validated against an exact key set");
  assert.match(keySet[1], /"cancelMarker"/u, "the marker must be a required spec field");

  assert.match(
    worker,
    /validSandboxRelativePath\(value\.cancelMarker\)/u,
    "the marker name must pass the same containment check as every other spec path",
  );
});

/**
 * The App resolves the name before it can build a render request.
 *
 * Ordering is the point: if the contract cannot be read the render never
 * starts, rather than starting a render that quietly ignores the button.
 */
test("the App takes the marker name from the contract and puts it on the wire", async () => {
  const [studio, orchestrator, declared] = await Promise.all([
    read("studio"),
    read("orchestrator"),
    contract(),
  ]);

  assert.doesNotMatch(
    studio,
    new RegExp(escapeForRegExp(declared.markerFileName), "u"),
    "the studio must read the marker name, never restate it",
  );
  assert.doesNotMatch(
    studio,
    /MOTION_CANCEL_FILE:\s*&str\s*=/u,
    "the marker literal must not come back as a Rust constant",
  );
  assert.match(
    studio,
    /contracts\/video\/motion-render-cancel-marker\.v1\.json/u,
    "the studio must read the declared contract",
  );

  assert.match(
    orchestrator,
    /"cancelMarker":/u,
    "the render request must carry the marker name to the Worker",
  );
  assert.doesNotMatch(
    orchestrator,
    new RegExp(escapeForRegExp(declared.markerFileName), "u"),
    "the transport passes the name through; it does not know it",
  );
});

/**
 * Cancellation is the App's control channel over its own child process.
 *
 * The authoring submission describes what to render. Letting it name the file
 * that stops a render would put the one input that decides whether the cancel
 * button works into a spec built from model output.
 */
test("the authoring side does not get to name the file that stops a render", async () => {
  const [agent, declared] = await Promise.all([read("agent"), contract()]);

  assert.match(
    agent,
    /def to_sandbox_spec/u,
    "the authoring submission still describes the render",
  );
  assert.doesNotMatch(
    agent,
    /cancelMarker/u,
    "the authoring spec must not carry the cancellation field",
  );
  assert.doesNotMatch(
    agent,
    new RegExp(escapeForRegExp(declared.markerFileName), "u"),
    "the authoring agent must not know the marker name at all",
  );
});

function escapeForRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, String.raw`\$&`);
}
