import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("A7-14 keeps failure counting, handoff, and authorization blocking atomic", async () => {
  const [authorization, convergence, schema, migration] = await Promise.all([
    readFile(
      new URL(
        "backend/src/automation_tool/control_plane/infrastructure/database/action_risk_authorization_repository.py",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "backend/src/automation_tool/control_plane/infrastructure/database/task_event_convergence_repository.py",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "backend/src/automation_tool/control_plane/infrastructure/database/schema.py",
        repositoryRoot,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "backend/migrations/versions/20260721_0023_action_failure_circuits.py",
        repositoryRoot,
      ),
      "utf8",
    ),
  ]);

  const exactReplay = authorization.indexOf("if existing_row is not None:");
  const circuitGate = authorization.indexOf("ActionRiskLimitReason.CONSECUTIVE_FAILURE_CIRCUIT");
  assert.ok(exactReplay >= 0);
  assert.ok(exactReplay < circuitGate);
  assert.match(convergence, /insert\(action_risk_results\)/u);
  assert.match(convergence, /TaskEventType\.TASK_AWAITING_HUMAN/u);
  assert.match(convergence, /message\.message_type != "task\.resumed"/u);
  assert.match(convergence, /opened_task_id != task_id/u);
  assert.match(schema, /action_failure_circuits = Table/u);
  assert.match(schema, /action_risk_results = Table/u);
  assert.match(schema, /fk_action_risk_results_authorization/u);
  assert.match(schema, /fk_action_failure_circuits_open_result/u);
  assert.match(migration, /revision: str = "20260721_0023"/u);
  assert.match(migration, /down_revision: str \| None = "20260720_0022"/u);
  assert.doesNotMatch(convergence, /playwright|BrowserRuntime|\.click\s*\(|\.fill\s*\(/u);
  assert.doesNotMatch(convergence, /cookie|storage_state|keychain|钥匙串/iu);
});
