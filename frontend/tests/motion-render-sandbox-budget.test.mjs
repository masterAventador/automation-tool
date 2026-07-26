import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

const SOURCES = {
  acceptance: "scripts/run_bm_16_acceptance.py",
  agent: "backend/src/automation_tool/executor/motion_authoring/agent.py",
  contract: "contracts/video/motion-render-sandbox-budget.v1.json",
  rust: "frontend/src-tauri/src/local_video_orchestrator.rs",
  sandboxTest: "scripts/test_motion_video_render_sandbox.py",
  worker: "workers/motion_composition/worker.mjs",
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

function constant(source, pattern, label) {
  const match = source.match(pattern);
  assert.ok(match, `missing declaration of ${label}`);
  return Number(match[1]);
}

test("the render sandbox CPU budget contract has one definition in every language", async () => {
  const sources = await readSources();
  const contract = JSON.parse(sources.contract);
  assert.equal(contract.version, "motion-render-sandbox-budget.v1");

  const wallSeconds = contract.wallClockSecondsMaximum;
  const parallelism = contract.cpuParallelismMaximum;
  assert.ok(
    Number.isInteger(wallSeconds) && wallSeconds > 0,
    "the contract must declare an integer wall-clock ceiling",
  );
  assert.ok(
    Number.isInteger(parallelism) && parallelism > 1,
    "a parallelism of one would collapse the CPU ceiling back onto wall clock",
  );
  assert.equal(
    contract.cpuSecondsAbsoluteMaximum,
    wallSeconds * parallelism,
    "the absolute CPU ceiling is the product of the two declared factors",
  );

  assert.equal(
    constant(
      sources.worker,
      /^const SANDBOX_SECONDS_MAXIMUM = (\d+);$/mu,
      "SANDBOX_SECONDS_MAXIMUM in the Worker",
    ),
    wallSeconds,
  );
  assert.equal(
    constant(
      sources.worker,
      /^const SANDBOX_CPU_PARALLELISM_MAXIMUM = (\d+);$/mu,
      "SANDBOX_CPU_PARALLELISM_MAXIMUM in the Worker",
    ),
    parallelism,
  );
  assert.equal(
    constant(
      sources.rust,
      /^const SANDBOX_SECONDS_MAXIMUM: u32 = (\d+);$/mu,
      "SANDBOX_SECONDS_MAXIMUM in the Rust orchestrator",
    ),
    wallSeconds,
  );
  assert.equal(
    constant(
      sources.rust,
      /^const SANDBOX_CPU_PARALLELISM_MAXIMUM: u32 = (\d+);$/mu,
      "SANDBOX_CPU_PARALLELISM_MAXIMUM in the Rust orchestrator",
    ),
    parallelism,
  );
  assert.equal(
    constant(
      sources.agent,
      /^SANDBOX_WALL_SECONDS_MAXIMUM: Final = (\d+)$/mu,
      "SANDBOX_WALL_SECONDS_MAXIMUM in the authoring agent",
    ),
    wallSeconds,
  );
  assert.equal(
    constant(
      sources.agent,
      /^SANDBOX_CPU_PARALLELISM_MAXIMUM: Final = (\d+)$/mu,
      "SANDBOX_CPU_PARALLELISM_MAXIMUM in the authoring agent",
    ),
    parallelism,
  );
  assert.equal(
    constant(
      sources.sandboxTest,
      /^SANDBOX_CPU_PARALLELISM_MAXIMUM = (\d+)$/mu,
      "SANDBOX_CPU_PARALLELISM_MAXIMUM in the sandbox boundary test",
    ),
    parallelism,
  );
});

test("the CPU ceiling is derived from the wall-clock budget, never shared with it", async () => {
  const sources = await readSources();

  assert.doesNotMatch(
    sources.worker,
    /maxCpuSeconds,\s*1,\s*SANDBOX_SECONDS_MAXIMUM/u,
    "the Worker must not bound CPU seconds by the wall-clock constant",
  );
  assert.match(
    sources.worker,
    /maxCpuSeconds[\s\S]{0,200}SANDBOX_CPU_PARALLELISM_MAXIMUM/u,
    "the Worker must bound CPU seconds by wall clock times parallelism",
  );
  assert.doesNotMatch(
    sources.rust,
    /SANDBOX_SECONDS_MAXIMUM\)\.contains\(&max_cpu_seconds\)/u,
    "the Rust orchestrator must not bound CPU seconds by the wall-clock constant",
  );
  assert.match(
    sources.rust,
    /SANDBOX_CPU_PARALLELISM_MAXIMUM[\s\S]{0,200}contains\(&max_cpu_seconds\)/u,
    "the Rust orchestrator must bound CPU seconds by wall clock times parallelism",
  );

  // No caller may invent its own CPU number next to the contract.
  assert.doesNotMatch(
    sources.acceptance,
    /RENDER_CPU_BUDGET_SECONDS\s*=\s*\d+/u,
    "the BM-16 acceptance must derive its CPU budget from the wall budget",
  );
  assert.doesNotMatch(
    sources.agent,
    /"maxCpuSeconds":\s*max\(1,\s*min\(\d+/u,
    "the authoring agent must derive its CPU budget from the wall budget",
  );
});
