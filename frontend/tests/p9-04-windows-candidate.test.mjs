import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const frontendRoot = new URL("../", import.meta.url);
const repositoryRoot = new URL("../../", import.meta.url);

test("P9-04 owns a least-privilege Windows NSIS candidate and isolated install acceptance", async () => {
  const [
    packageSource,
    productionConfigSource,
    candidateConfigSource,
    capabilitySource,
    runnerSource,
    workflowSource,
  ] = await Promise.all([
    readFile(new URL("package.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/tauri.conf.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/tauri.windows-candidate.conf.json", frontendRoot), "utf8"),
    readFile(new URL("src-tauri/capabilities/main.json", frontendRoot), "utf8"),
    readFile(new URL("scripts/run_p9_04_acceptance.py", repositoryRoot), "utf8"),
    readFile(new URL(".github/workflows/desktop.yml", repositoryRoot), "utf8"),
  ]);
  const packageDocument = JSON.parse(packageSource);
  const production = JSON.parse(productionConfigSource);
  const candidate = JSON.parse(candidateConfigSource);
  const capability = JSON.parse(capabilitySource);

  assert.equal(
    packageDocument.scripts["test:p9-04-windows-package"],
    "uv run --project ../backend --locked python ../scripts/run_p9_04_acceptance.py",
  );
  assert.equal(production.app.withGlobalTauri, false);
  assert.deepEqual(production.app.security.capabilities, ["main"]);
  assert.equal(production.app.security.dangerousDisableAssetCspModification, false);
  assert.equal(production.app.security.csp["default-src"], "'self'");
  assert.equal(production.app.security.csp["connect-src"], "ipc: http://ipc.localhost");
  assert.equal(production.plugins.updater.windows.installMode, "passive");
  assert.deepEqual(capability.permissions, []);
  assert.equal(candidate.bundle.active, true);
  assert.deepEqual(candidate.bundle.targets, ["nsis"]);
  assert.equal(candidate.bundle.createUpdaterArtifacts, undefined);
  assert.equal(candidate.bundle.windows.nsis.installMode, "currentUser");
  assert.equal(candidate.bundle.windows.certificateThumbprint, undefined);
  assert.equal(candidate.bundle.windows.signCommand, undefined);
  assert.equal(candidate.productName, undefined);
  assert.equal(candidate.identifier, undefined);
  assert.equal(candidate.mainBinaryName, undefined);
  assert.equal(candidate.app, undefined);
  assert.equal(candidate.plugins, undefined);

  assert.match(runnerSource, /sys\.platform\s*!=\s*["']win32["']/u);
  assert.match(runnerSource, /require_non_elevated_process/u);
  assert.match(runnerSource, /build_windows_executor_candidate/u);
  assert.match(runnerSource, /write_signed_executor_manifest/u);
  assert.match(runnerSource, /local-executor\/package/u);
  assert.match(runnerSource, /os\.path\.relpath\(executor,\s*TAURI_ROOT\)/u);
  assert.match(runnerSource, /TemporaryDirectory\([^)]*dir=/su);
  assert.doesNotMatch(runnerSource, /os\.fspath\(executor\).*os\.sep/u);
  assert.match(runnerSource, /["']--bundles["'],\s*["']nsis["']/u);
  assert.doesNotMatch(runnerSource, /["']--debug["']|["']--features["']/u);
  assert.match(runnerSource, /audit-production-package\.mjs/u);
  assert.match(runnerSource, /Get-AuthenticodeSignature/u);
  assert.match(runnerSource, /\[Console\]::In\.ReadToEnd\(\)/u);
  assert.match(runnerSource, /hasSigner/u);
  assert.match(runnerSource, /hasTimestamp/u);
  assert.match(runnerSource, /NotSigned/u);
  assert.match(runnerSource, /BUNDLE_TYPE_VAR_UNK/u);
  assert.match(runnerSource, /BUNDLE_TYPE_VAR_NSS/u);
  assert.match(runnerSource, /LOCALAPPDATA/u);
  assert.match(runnerSource, /HKEY_CURRENT_USER/u);
  assert.match(runnerSource, /HKEY_LOCAL_MACHINE/u);
  assert.match(runnerSource, /parse_windows_registry_path/u);
  assert.match(
    runnerSource,
    /OpenKey\(\s*parent,\s*winreg\.EnumKey\(parent,\s*index\),\s*0,\s*winreg\.KEY_READ\s*\|\s*view/su,
  );
  assert.match(runnerSource, /DisplayName/u);
  assert.match(runnerSource, /DisplayVersion/u);
  assert.match(runnerSource, /InstallLocation/u);
  assert.match(runnerSource, /UninstallString/u);
  assert.match(runnerSource, /["']\/S["']/u);
  assert.match(runnerSource, /uninstall\.exe/u);
  assert.match(runnerSource, /audit_windows_executor_candidate/u);
  assert.match(runnerSource, /verify_manifest_signature/u);
  assert.doesNotMatch(runnerSource, /AUTOMATION_TOOL_UPDATE_INSTALL_PROBE/u);
  assert.doesNotMatch(runnerSource, /subprocess\.Popen/u);
  assert.doesNotMatch(runnerSource, /subprocess\.(?:Popen|run)\([^)]*(?:chrome|edge)/iu);

  assert.match(workflowSource, /frontend\/\*\*/u);
  assert.match(workflowSource, /run_p9_04_acceptance\.py/u);
  assert.match(workflowSource, /runner\.os\s*==\s*'Windows'/u);
});
