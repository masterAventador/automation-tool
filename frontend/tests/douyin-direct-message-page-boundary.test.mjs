import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("A7-09 owns direct-message selectors and permissions without executing actions", async () => {
  const source = await readFile(
    new URL(
      "backend/src/automation_tool/executor/rpa/douyin/direct_message_page.py",
      repositoryRoot,
    ),
    "utf8",
  );

  assert.match(source, /def enter_conversation/u);
  assert.match(source, /def message_input/u);
  assert.match(source, /def message_send/u);
  assert.match(source, /MESSAGING_NOT_ALLOWED/u);
  assert.match(source, /FOLLOW_REQUIRED/u);
  assert.match(source, /def final_confirmation/u);
  assert.doesNotMatch(source, /\.click\(|\.fill\(|\.press\(/u);
});
