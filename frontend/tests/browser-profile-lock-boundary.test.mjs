import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-06 keeps Profile locking native, crash-safe, and absent from WebView commands", async () => {
  const [common, unix, windows, native] = await Promise.all([
    readRepositoryFile("frontend/src-tauri/src/browser_profiles.rs"),
    readRepositoryFile("frontend/src-tauri/src/browser_profiles_unix.rs"),
    readRepositoryFile("frontend/src-tauri/src/browser_profiles_windows.rs"),
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
  ]);

  assert.match(common, /try_acquire_lock/u);
  assert.match(common, /RecoveryRequired/u);
  assert.match(unix, /LOCK_EX\s*\|\s*libc::LOCK_NB/u);
  assert.match(windows, /LockFileEx/u);
  assert.match(unix, /\.automation-tool-profile-lock-v1/u);
  assert.match(windows, /\.automation-tool-profile-lock-v1/u);
  assert.match(windows, /name != PROFILE_LOCK_FILE/u);
  assert.match(windows, /FILE_SYNCHRONOUS_IO_NONALERT/u);
  assert.doesNotMatch(native, /tauri::command[\s\S]{0,300}(lock|unlock).*profile/u);
  assert.doesNotMatch(common, /force_unlock|recover_lock|clear_stale/u);
});
