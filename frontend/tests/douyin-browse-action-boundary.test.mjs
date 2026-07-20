import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("A7-10 keeps browse single-shot, read-only, and Executor-local", async () => {
  const [browse, profilePage, pageVersion] = await Promise.all([
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/browse.py"),
    readRepositoryFile(
      "backend/src/automation_tool/executor/rpa/douyin/profile_page.py",
    ),
    readRepositoryFile(
      "backend/src/automation_tool/executor/rpa/douyin/page_version.py",
    ),
  ]);

  assert.match(browse, /douyin\.browse-execution\.v1/u);
  assert.match(browse, /DouyinCandidate/u);
  assert.match(browse, /DouyinProfilePage/u);
  assert.match(browse, /douyin_user_profile_url/u);
  assert.match(browse, /wait_until="domcontentloaded"/u);
  assert.match(browse, /cancellation_requested/u);
  assert.match(browse, /_executed/u);
  assert.match(profilePage, /douyin\.profile-page\.v1/u);
  assert.match(pageVersion, /def douyin_user_profile_url/u);
  assert.doesNotMatch(browse, /https:\/\/www\.douyin\.com/u);
  assert.doesNotMatch(browse, /(?:aria-label|data-e2e|role=|placeholder=)/u);
  assert.doesNotMatch(browse, /\.locator\s*\(/u);
  assert.doesNotMatch(browse, /\.(?:click|fill|press)\s*\(/u);
  assert.doesNotMatch(
    browse,
    /(?:\.evaluate\s*\(|context\.cookies|document\.cookie|storage_state|localStorage|sessionStorage)/iu,
  );
  assert.doesNotMatch(browse, /comment_page|direct_message_page|control_plane|httpx|requests\./iu);
  assert.doesNotMatch(profilePage, /(?:发表评论|发送私信|comment-|direct-message)/u);
  assert.doesNotMatch(profilePage, /\.(?:click|fill|press)\s*\(/u);
});
