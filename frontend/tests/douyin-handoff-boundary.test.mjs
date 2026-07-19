import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-11 maps every platform challenge to manual handoff without solving it", async () => {
  const [login, session, protocol] = await Promise.all([
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/login.py"),
    readRepositoryFile("backend/src/automation_tool/executor/rpa/douyin/session.py"),
    readRepositoryFile("backend/src/automation_tool/protocol/executor_envelope.py"),
  ]);

  assert.match(login, /HANDOFF_REQUIRED\s*=\s*"handoff_required"/u);
  assert.match(login, /DouyinSessionState\.RISK[\s\S]{0,300}HANDOFF_REQUIRED/u);
  assert.match(login, /def recheck\(self\)/u);
  assert.match(session, /verifycenter\/captcha/u);
  assert.match(protocol, /"handoff\.requested"/u);
  assert.doesNotMatch(
    `${login}\n${session}`,
    /\.click\(|\.fill\(|\.press\(|\.drag_and_drop\(|captcha_code|solve_captcha|bypass/u,
  );
});
