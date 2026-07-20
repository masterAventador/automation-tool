import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("A7-07 keeps the durable side-effect state machine inside the private Executor ledger", async () => {
  const stateSource = await readFile(
    new URL(
      "backend/src/automation_tool/executor/side_effect_ledger.py",
      repositoryRoot,
    ),
    "utf8",
  );

  const ledgerSource = await readFile(
    new URL("backend/src/automation_tool/executor/ledger.py", repositoryRoot),
    "utf8",
  );

  assert.match(stateSource, /class SideEffectState\(StrEnum\)/u);
  assert.match(stateSource, /PREPARED = "prepared"/u);
  assert.match(stateSource, /DISPATCHED = "dispatched"/u);
  assert.match(stateSource, /VERIFIED = "verified"/u);
  assert.match(stateSource, /UNCERTAIN = "uncertain"/u);
  assert.match(ledgerSource, /CREATE TABLE executor_side_effects/u);
  assert.match(ledgerSource, /def prepare_side_effect/u);
  assert.match(ledgerSource, /def begin_side_effect_dispatch/u);
  assert.match(ledgerSource, /def verify_side_effect/u);
  assert.match(ledgerSource, /def mark_side_effect_uncertain/u);
  assert.doesNotMatch(
    ledgerSource,
    /executor_side_effects[\s\S]{0,1600}(?:message|content|credential|token)\s+TEXT/iu,
  );
});
