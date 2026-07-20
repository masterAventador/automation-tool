import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

test("A7-08 owns comment selectors in one non-executing production Page Object", async () => {
  const source = await readFile(
    new URL(
      "backend/src/automation_tool/executor/rpa/douyin/comment_page.py",
      repositoryRoot,
    ),
    "utf8",
  );

  assert.match(source, /DOUYIN_COMMENT_PAGE_SELECTOR_VERSION/u);
  assert.match(source, /def comment_input/u);
  assert.match(source, /def comment_submit/u);
  assert.match(source, /def final_confirmation/u);
  assert.match(source, /def wait_for_final/u);
  assert.doesNotMatch(source, /\.click\(|\.fill\(|\.press\(/u);
  assert.doesNotMatch(source, /Cookie|storage_state|localStorage|OCR|LLM/u);
});
