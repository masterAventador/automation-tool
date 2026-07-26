import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

const SOURCES = {
  agent: "backend/src/automation_tool/executor/motion_authoring/agent.py",
  contract: "contracts/video/motion-authoring-model-call.v1.json",
  studio: "frontend/src/features/video-studio/motion-model-call.ts",
  card: "frontend/src/features/video-studio/VideoStudio.tsx",
  spec: "backend/automation-tool-executor.spec",
  prerequisites: "scripts/test_desktop_e2e_prerequisites.py",
};

const read = (name) => readFile(new URL(SOURCES[name], repositoryRoot), "utf8");

/**
 * How long the video-creation model may go quiet before we stop waiting.
 *
 * The number lived only in `agent.py`, which is why the card could not say it:
 * writing "6 分" into a sentence in the App would have been a hand-copied
 * second version of a value nothing checked, and the two would have drifted the
 * first time the budget was tuned. So the card said "超过允许的最长等待时间",
 * which tells the user nothing they can act on.
 *
 * The fix is the same one `motion-storyboard-duration.v1` already uses for the
 * film length: one file holds the number and both languages read it. This test
 * is what keeps it that way — a literal reintroduced on either side fails here
 * rather than being noticed the day the two disagree.
 */
test("the authoring model's idle timeout is written down exactly once", async () => {
  const agent = await read("agent");

  assert.doesNotMatch(
    agent,
    /^MODEL_TIMEOUT_SECONDS: Final = \d+$/mu,
    "the timeout must be loaded from the shared contract, not written here as a literal",
  );

  const contract = JSON.parse(await read("contract"));
  assert.equal(contract.version, "motion-authoring-model-call.v1");
  const seconds = contract.streamIdleTimeoutSeconds;
  assert.ok(
    Number.isInteger(seconds) && seconds > 0,
    "the contract must declare a positive whole number of seconds",
  );

  assert.match(
    agent,
    /motion-authoring-model-call\.v1\.json/u,
    "the authoring agent must read the timeout from the contract",
  );

  const studio = await read("studio");
  assert.match(
    studio,
    /contracts\/video\/motion-authoring-model-call\.v1\.json/u,
    "the App must take the timeout from the same contract the agent reads",
  );
  assert.doesNotMatch(
    studio,
    new RegExp(String.raw`\b${seconds}\b`, "u"),
    "the App must derive the number, never restate it",
  );
});

/**
 * The sentence on the card is built from the contract, not typed next to it.
 *
 * A number pasted into the copy would satisfy the user-facing test in
 * `VideoStudio.test.tsx` — it only checks that some figure is shown — while
 * being exactly the second source this task exists to remove.
 */
test("the card's wait is rendered from the shared value", async () => {
  const [card, contract] = await Promise.all([
    read("card"),
    read("contract").then(JSON.parse),
  ]);
  const seconds = contract.streamIdleTimeoutSeconds;

  assert.match(
    card,
    /MOTION_AUTHORING_IDLE_WAIT/u,
    "the timed-out sentence must be composed from the shared value",
  );
  assert.doesNotMatch(
    card,
    new RegExp(String.raw`\b${seconds}\b`, "u"),
    "the card must not restate the number of seconds",
  );
  assert.doesNotMatch(
    card,
    /允许的最长等待时间/u,
    "the vague wording exists only because the number could not be named",
  );
});

/**
 * A contract the agent reads at startup has to be in the package the user gets.
 *
 * `agent.py` loads this file at import time, so an Executor built without it
 * does not fail at the timeout — it fails to start at all, and only for the
 * user, because a source checkout always has the file sitting in the
 * repository. That is the shape this project has already shipped once.
 */
test("the packaged Executor carries the contract it reads at startup", async () => {
  const [spec, prerequisites] = await Promise.all([
    read("spec"),
    read("prerequisites"),
  ]);

  assert.match(
    spec,
    /"contracts\/video\/motion-authoring-model-call\.v1\.json"/u,
    "the Executor package must include the contract the authoring agent reads",
  );
  assert.match(
    prerequisites,
    /"contracts\/video\/motion-authoring-model-call\.v1\.json"/u,
    "the packaging inputs must list it too, or a cached build silently drops it",
  );
});
