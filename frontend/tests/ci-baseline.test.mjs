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

function assertReadOnlyValidationWorkflow(workflow) {
  assert.match(workflow, /^permissions:\n {2}contents: read$/m);
  assert.doesNotMatch(workflow, /\$\{\{\s*secrets\./);
  assert.doesNotMatch(workflow, /\b(deploy|publish|release|upload-artifact)\b/i);
  assert.doesNotMatch(workflow, /^\s+(contents|packages|id-token|pull-requests): write$/m);
}

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
  assert.match(workflow, /pnpm install --frozen-lockfile/);
  assert.match(workflow, /pnpm test:ui/);
  assert.match(workflow, /pnpm check:production-boundaries/);
  assert.match(workflow, /libwebkit2gtk-4\.1-dev/);
  assert.match(workflow, /libayatana-appindicator3-dev/);
  assert.match(workflow, /cargo test --manifest-path src-tauri\/Cargo\.toml --locked/);
  assert.match(workflow, /cargo clippy .* -- -D warnings/);
});

test("desktop CI builds and smokes both supported desktop platforms", async () => {
  const workflow = await readRepositoryFile(".github/workflows/desktop.yml");

  assertActionsArePinned(workflow);
  assertReadOnlyValidationWorkflow(workflow);
  assert.match(workflow, /runner:\s*\[macos-latest, windows-latest\]/);
  assert.match(workflow, /runs-on:\s*\$\{\{\s*matrix\.runner\s*\}\}/);
  assert.match(workflow, /fail-fast:\s*false/);
  assert.match(workflow, /pnpm tauri build --debug --no-bundle/);
  assert.match(workflow, /pnpm test:tauri/);
  assert.match(workflow, /pnpm check:production-boundaries/);
  assert.match(workflow, /timeout-minutes:\s*45/);
});
