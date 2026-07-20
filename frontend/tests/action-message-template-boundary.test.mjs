import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("A7-05 keeps one closed action message variable across every production boundary", async () => {
  const [pythonPolicy, domain, gateway, form, rustBridge, schema, migration] =
    await Promise.all([
      readRepositoryFile(
        "backend/src/automation_tool/protocol/action_message_template.py",
      ),
      readRepositoryFile(
        "backend/src/automation_tool/control_plane/domain/task_definitions.py",
      ),
      readRepositoryFile(
        "frontend/src/api/control-plane/douyin-search-exposure.ts",
      ),
      readRepositoryFile("frontend/src/features/task-create/TaskCreate.tsx"),
      readRepositoryFile("frontend/src-tauri/src/control_plane.rs"),
      readRepositoryFile(
        "backend/src/automation_tool/control_plane/infrastructure/database/schema.py",
      ),
      readRepositoryFile(
        "backend/migrations/versions/20260720_0021_action_message_template_policy.py",
      ),
    ]);

  assert.match(pythonPolicy, /ACTION_MESSAGE_TEMPLATE_VERSION.*action-message-template\.v1/u);
  assert.match(pythonPolicy, /MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS.*500/u);
  assert.match(pythonPolicy, /TARGET_DISPLAY_NAME = "target_display_name"/u);
  assert.match(pythonPolicy, /not literal\.strip\(\) or "\{" in literal or "\}" in literal/u);
  assert.match(domain, /ActionMessageTemplate\(source=self\.message_template\)/u);

  assert.match(gateway, /MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS = 500/u);
  assert.match(gateway, /variable !== "target_display_name"/u);
  assert.match(gateway, /literal\.includes\("\{"\)/u);
  assert.match(form, /\{\{target_display_name\}\}/u);

  assert.match(rustBridge, /TARGET_DISPLAY_NAME_VARIABLE.*\{\{target_display_name\}\}/u);
  assert.match(rustBridge, /require_action_message_template\(message\)/u);
  assert.match(rustBridge, /literal\.contains\('\{'\).*literal\.contains\('\}'\)/u);

  for (const databasePolicy of [schema, migration]) {
    assert.match(
      databasePolicy,
      /replace\(message_template, '\{\{target_display_name\}\}', ''\)/u,
    );
    assert.match(databasePolicy, /!~ '\[\{\}\]'/u);
  }
});

test("A7-05 validates but never evaluates or renders action message templates", async () => {
  const [pythonPolicy, gateway, rustBridge, appAcceptance, acceptanceRunner] =
    await Promise.all([
      readRepositoryFile(
        "backend/src/automation_tool/protocol/action_message_template.py",
      ),
      readRepositoryFile(
        "frontend/src/api/control-plane/douyin-search-exposure.ts",
      ),
      readRepositoryFile("frontend/src-tauri/src/control_plane.rs"),
      readRepositoryFile("frontend/e2e-tauri/task-create-form.spec.ts"),
      readRepositoryFile("scripts/run_t3_17_acceptance.py"),
    ]);

  const gatewayPolicy = gateway.match(
    /export const douyinActionMessageTemplateSchema[\s\S]*?\n\}\);/u,
  );
  const rustPolicy = rustBridge.match(
    /fn require_action_message_template[\s\S]*?\n\}/u,
  );
  assert.ok(gatewayPolicy);
  assert.ok(rustPolicy);
  for (const implementation of [pythonPolicy, gatewayPolicy[0], rustPolicy[0]]) {
    assert.doesNotMatch(
      implementation,
      /\beval\b|new Function|jinja|handlebars|mustache|llm/iu,
    );
  }
  assert.match(appAcceptance, /\{\{unknown\}\}/u);
  assert.match(appAcceptance, /\{\{target_display_name\}\}/u);
  assert.match(acceptanceRunner, /\{\{target_display_name\}\}/u);
});
