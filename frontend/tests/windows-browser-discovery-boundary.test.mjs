import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function readRepositoryFile(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("B5-03 keeps Windows browser discovery behind Win32 trust APIs and fixed products", async () => {
  const [common, windows, cargo, integration] = await Promise.all([
    readRepositoryFile("frontend/src-tauri/src/browser_discovery.rs"),
    readRepositoryFile("frontend/src-tauri/src/browser_discovery_windows.rs"),
    readRepositoryFile("frontend/src-tauri/Cargo.toml"),
    readRepositoryFile("frontend/src-tauri/tests/browser_discovery.rs"),
  ]);

  for (const api of [
    "discover_windows_browsers",
    "revalidate_windows_browser",
    "TrustedWindowsBrowser",
  ]) {
    assert.match(common, new RegExp(api, "u"));
  }

  for (const feature of [
    "Win32_Security_Cryptography",
    "Win32_Security_Cryptography_Catalog",
    "Win32_Security_Cryptography_Sip",
    "Win32_Security_WinTrust",
    "Win32_System_Com",
    "Win32_System_Registry",
    "Win32_UI_Shell",
  ]) {
    assert.match(cargo, new RegExp(feature, "u"));
  }

  for (const fixedBoundary of [
    "SOFTWARE\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\App Paths\\\\chrome.exe",
    "SOFTWARE\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\App Paths\\\\msedge.exe",
    "Google\\\\Chrome\\\\Application\\\\chrome.exe",
    "Microsoft\\\\Edge\\\\Application\\\\msedge.exe",
    "Google Chrome",
    "Google LLC",
    "Microsoft Edge",
    "Microsoft Corporation",
    "WinVerifyTrust",
    "WTHelperProvDataFromStateData",
    "CertGetNameStringW",
    "GetFileVersionInfoW",
    "RegGetValueW",
    "SHGetKnownFolderPath",
    "GetFileInformationByHandle",
    "GetFinalPathNameByHandleW",
    "FILE_ATTRIBUTE_REPARSE_POINT",
    "FILE_FLAG_OPEN_REPARSE_POINT",
    "FILE_SHARE_READ",
  ]) {
    assert.ok(
      windows.includes(fixedBoundary),
      `Windows discovery must retain the fixed boundary: ${fixedBoundary}`,
    );
  }

  assert.doesNotMatch(windows, /std::process::Command|powershell|cmd\.exe|HOME|USERPROFILE/u);
  assert.match(windows, /hFile:\s*file\.as_raw_handle\(\) as HANDLE/u);
  assert.match(integration, /real_installed_windows_browsers_use_the_production_authenticode_path/u);
  assert.match(integration, /revalidate_windows_browser/u);
});
