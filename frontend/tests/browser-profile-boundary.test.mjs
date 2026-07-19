import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-05 keeps Profile creation inside Rust without a WebView path command", async () => {
  const [library, profiles, windowsProfiles] = await Promise.all([
    readRepositoryFile("frontend/src-tauri/src/lib.rs"),
    readRepositoryFile("frontend/src-tauri/src/browser_profiles.rs"),
    readRepositoryFile("frontend/src-tauri/src/browser_profiles_windows.rs"),
  ]);

  assert.match(library, /pub mod browser_profiles;/u);
  assert.match(
    library,
    /app\.manage\(browser_profiles::BrowserProfileStore::initialize\(\s*&app_data_directory/u,
  );
  assert.doesNotMatch(library, /tauri::command[\s\S]{0,300}(create|open)_.*profile/u);
  assert.match(profiles, /const PROFILE_ROOT_DIRECTORY: &str = "browser-profiles";/u);
  assert.match(profiles, /const DOUYIN_DIRECTORY: &str = "douyin";/u);
  assert.doesNotMatch(profiles, /create_dir_all|remove_dir_all|canonicalize/u);
  assert.doesNotMatch(profiles, /xiaohongshu|kuaishou|wechat|cookie|account_id/u);
  assert.match(windowsProfiles, /PrivateSecurityDescriptor::new\(DIRECTORY_ACE_FLAGS\)/u);
  assert.match(windowsProfiles, /map_or\(null\(\), PrivateSecurityDescriptor::as_ptr\)/u);
  assert.match(windowsProfiles, /SecurityDescriptor: security_descriptor/u);
  assert.match(windowsProfiles, /SetSecurityDescriptorControl/u);
});
