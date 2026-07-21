import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("A7-12 keeps message sending ordered, single-shot, and Executor-local", async () => {
  const source = await readFile(
    new URL(
      "backend/src/automation_tool/executor/rpa/douyin/direct_message_action.py",
      repositoryRoot,
    ),
    "utf8",
  );

  const admission = source.indexOf("self._action_gate.admit(");
  const prepared = source.indexOf("self._ledger.prepare_side_effect(");
  const entry = source.indexOf("entry.click(");
  const fill = source.indexOf("message_input.fill(");
  const dispatch = source.indexOf("self._ledger.begin_side_effect_dispatch(");
  const click = source.indexOf("message_send.click(");
  const verify = source.indexOf("self._ledger.verify_side_effect(");

  assert.match(source, /douyin\.direct-message-action-execution\.v1/u);
  assert.match(source, /DouyinDirectMessageActionReceipt/u);
  assert.match(source, /ActionAuthorizationExpectation/u);
  assert.match(source, /ActionMessageTemplate/u);
  assert.match(source, /DouyinCandidateSummary/u);
  assert.match(source, /DouyinDirectMessagePage/u);
  assert.ok(admission >= 0);
  assert.ok(admission < prepared);
  assert.ok(prepared < entry);
  assert.ok(entry < fill);
  assert.ok(fill < dispatch);
  assert.ok(dispatch < click);
  assert.ok(click < verify);
  assert.equal(source.match(/entry\.click\(/gu)?.length, 1);
  assert.equal(source.match(/message_input\.fill\(/gu)?.length, 1);
  assert.equal(source.match(/message_send\.click\(/gu)?.length, 1);
  assert.match(source, /READY_MESSAGING_NOT_ALLOWED/u);
  assert.match(source, /READY_FOLLOW_REQUIRED/u);
  assert.match(source, /mark_side_effect_uncertain/u);
  assert.match(source, /REPLAY_VERIFIED/u);
  assert.match(source, /REPLAY_UNCERTAIN/u);
  assert.doesNotMatch(source, /https:\/\/www\.douyin\.com/u);
  assert.doesNotMatch(source, /(?:aria-label|data-e2e|role=|placeholder=)/u);
  assert.doesNotMatch(source, /\.locator\s*\(/u);
  assert.doesNotMatch(
    source,
    /(?:context\.cookies|document\.cookie|storage_state|localStorage|sessionStorage)/iu,
  );
  assert.doesNotMatch(source, /control_plane|httpx|requests\.|OCR|LLM/iu);
});
