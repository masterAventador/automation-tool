import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("A7-04 keeps signed authority and durable local hard limits as two independent gates", async () => {
  const [gate, ledger] = await Promise.all([
    readRepositoryFile("backend/src/automation_tool/executor/action_gate.py"),
    readRepositoryFile("backend/src/automation_tool/executor/ledger.py"),
  ]);

  assert.match(gate, /class ExecutorActionGate/u);
  assert.match(gate, /ledger\.bind_action_hard_policy/u);
  assert.ok(gate.indexOf("self._verifier.verify") < gate.indexOf("self._ledger.admit_action"));
  assert.match(gate, /minimum_interval_seconds=int\(self\._policy/u);
  assert.match(gate, /task_action_limit=self\._policy\.task_action_limit/u);
  assert.doesNotMatch(
    gate,
    /claims\.(?:minimum_interval|task_action_limit)|expected\.(?:minimum_interval|task_action_limit)/u,
  );

  assert.match(ledger, /PRAGMA user_version = 4/u);
  for (const table of [
    "executor_action_guard",
    "executor_action_policy",
    "executor_action_admissions",
  ]) {
    assert.match(ledger, new RegExp(`CREATE TABLE ${table}`, "u"));
  }
  assert.match(ledger, /BEGIN IMMEDIATE/u);
  assert.match(ledger, /authorization_fingerprint BLOB NOT NULL/u);
  const actionAdmissionSchema = ledger.match(
    /CREATE TABLE executor_action_admissions \([\s\S]*?\n {8}\)/u,
  );
  assert.ok(actionAdmissionSchema);
  assert.doesNotMatch(
    actionAdmissionSchema[0],
    /authorization_token|cookie|profile_path|private_key|password/u,
  );
});

test("A7-04 exposes no server-controlled threshold on the action admission call", async () => {
  const gate = await readRepositoryFile(
    "backend/src/automation_tool/executor/action_gate.py",
  );
  const admitSignature = gate.match(
    /def admit\([\s\S]*?\n {4}\) -> LocalActionAdmission:/u,
  );

  assert.ok(admitSignature);
  assert.match(admitSignature[0], /token: str/u);
  assert.match(admitSignature[0], /expected: ActionAuthorizationExpectation/u);
  assert.doesNotMatch(admitSignature[0], /minimum_interval|task_action_limit/u);
});
