import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

function assertActionsArePinned(workflow) {
  const uses = [...workflow.matchAll(/^\s*-?\s*uses:\s*([^@\s]+)@([^\s#]+)(?:\s+#\s*(.+))?$/gm)];

  assert.ok(uses.length >= 4, "workflow must use the shared pinned setup actions");
  for (const [, action, reference, versionComment] of uses) {
    assert.match(reference, /^[0-9a-f]{40}$/, `${action} must use an immutable commit SHA`);
    assert.match(
      versionComment ?? "",
      /^(v\d|stable-)/,
      `${action} must retain its readable version tag`,
    );
  }
}

// Anything that would move a build off the runner. "release" used to be banned
// as a bare word, which stopped being workable once CI started running the
// read-only checks that guard the release path — those are named after it and
// publish nothing. It is banned as an action or a command instead, which is the
// property this guard was always protecting.
const distributionMarkers = [
  /\b(deploy|publish|upload-artifact)\b/i,
  /uses:\s*\S*release\S*@/i,
  /\bgh\s+release\b/i,
  /\b(cargo|npm|pnpm)\s+publish\b/i,
  /\brelease\s+(create|upload)\b/i,
];

function assertReadOnlyValidationWorkflow(workflow) {
  assert.match(workflow, /^permissions:\n {2}contents: read$/m);
  assert.doesNotMatch(workflow, /\$\{\{\s*secrets\./);
  for (const marker of distributionMarkers) {
    assert.doesNotMatch(workflow, marker);
  }
  assert.doesNotMatch(workflow, /^\s+(contents|packages|id-token|pull-requests): write$/m);
}

test("the read-only workflow guard still refuses anything that would distribute a build", async () => {
  const publishing = [
    "      - uses: actions/upload-artifact@0000000000000000000000000000000000000000 # v4",
    "      - uses: softprops/action-gh-release@0000000000000000000000000000000000000000 # v2",
    "      - run: gh release create v1.0.0 package.dmg",
    "      - run: npm publish",
    "      - run: ./deploy-test-macos.sh",
  ];
  for (const step of publishing) {
    assert.throws(
      () => assertReadOnlyValidationWorkflow(`permissions:\n  contents: read\n${step}\n`),
      undefined,
      step,
    );
  }
  // And it still accepts a workflow that only runs the read-only release gates.
  assert.doesNotThrow(() =>
    assertReadOnlyValidationWorkflow(
      "permissions:\n  contents: read\n      - run: python3 scripts/check_release_package_wiring.py\n",
    ),
  );
});

test("quality CI separates Backend, Frontend, and Rust gates", async () => {
  const workflow = await readRepositoryFile(".github/workflows/quality.yml");

  assertActionsArePinned(workflow);
  assertReadOnlyValidationWorkflow(workflow);
  assert.match(workflow, /^ {2}backend:$/m);
  assert.match(workflow, /^ {2}frontend:$/m);
  assert.match(workflow, /^ {2}rust:$/m);
  assert.match(workflow, /uv sync --locked --dev/);
  assert.match(workflow, /uv run pytest --cov=automation_tool --cov-report=term-missing/);
  assert.match(workflow, /uv run automation-tool-export-openapi .* --check/);
  assert.match(
    workflow,
    /uv run automation-tool-export-executor-schema .* --check/,
    "CI must reject Executor protocol schema drift",
  );
  assert.match(workflow, /pnpm install --frozen-lockfile/);
  assert.match(workflow, /pnpm test:ui/);
  assert.match(workflow, /pnpm check:production-boundaries/);
  assert.match(workflow, /libwebkit2gtk-4\.1-dev/);
  assert.match(workflow, /libayatana-appindicator3-dev/);
  assert.match(workflow, /cargo test --manifest-path src-tauri\/Cargo\.toml --locked/);
  assert.match(
    workflow,
    /cargo test .* --features control-plane-e2e/,
    "CI must compile and test the production-path Control Plane acceptance feature",
  );
  assert.match(workflow, /cargo clippy .* -- -D warnings/);
  assert.match(
    workflow,
    /cargo clippy .* --features control-plane-e2e -- -D warnings/,
    "CI must lint the production-path Control Plane acceptance feature",
  );
});

test("desktop CI builds and smokes both supported desktop platforms", async () => {
  const workflow = await readRepositoryFile(".github/workflows/desktop.yml");

  assertActionsArePinned(workflow);
  assertReadOnlyValidationWorkflow(workflow);
  assert.match(workflow, /runner:\s*\[macos-latest, windows-latest\]/);
  assert.match(workflow, /runs-on:\s*\$\{\{\s*matrix\.runner\s*\}\}/);
  assert.match(workflow, /fail-fast:\s*false/);
  assert.match(workflow, /python \.\.\/scripts\/run_e4_15_acceptance\.py/);
  assert.match(workflow, /pnpm test:tauri/);
  assert.match(workflow, /pnpm check:production-boundaries/);
  assert.match(workflow, /timeout-minutes:\s*45/);
});
