import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("A7-13 recovery is read-only and settles the existing ledger fact", async () => {
  const source = await readFile(
    new URL(
      "backend/src/automation_tool/executor/rpa/douyin/side_effect_recovery.py",
      repositoryRoot,
    ),
    "utf8",
  );

  const read = source.indexOf("self._ledger.get_side_effect(");
  const commentWait = source.indexOf("page.wait_for_final(");
  const verify = source.indexOf("self._ledger.verify_side_effect(");
  const uncertain = source.indexOf("self._ledger.mark_side_effect_uncertain(");

  assert.match(source, /douyin\.side-effect-recovery\.v1/u);
  assert.match(source, /DouyinCommentPage/u);
  assert.match(source, /DouyinDirectMessagePage/u);
  assert.match(source, /comment_action_verification_fingerprint/u);
  assert.match(source, /direct_message_action_verification_fingerprint/u);
  assert.ok(read >= 0);
  assert.ok(read < commentWait);
  assert.ok(commentWait < verify);
  assert.ok(commentWait < uncertain);
  assert.equal(source.match(/final_confirmation\(\)/gu)?.length, 2);
  assert.doesNotMatch(source, /\.click\s*\(/u);
  assert.doesNotMatch(source, /\.fill\s*\(/u);
  assert.doesNotMatch(source, /\.press\s*\(/u);
  assert.doesNotMatch(source, /enter_conversation|message_input|message_send/u);
  assert.doesNotMatch(source, /comment_input|comment_submit/u);
  assert.doesNotMatch(source, /https:\/\/www\.douyin\.com/u);
  assert.doesNotMatch(source, /(?:aria-label|data-e2e|role=|placeholder=)/u);
  assert.doesNotMatch(source, /\.locator\s*\(/u);
  assert.doesNotMatch(
    source,
    /(?:context\.cookies|document\.cookie|storage_state|localStorage|sessionStorage)/iu,
  );
  assert.doesNotMatch(source, /control_plane|httpx|requests\.|OCR|LLM/iu);
});
