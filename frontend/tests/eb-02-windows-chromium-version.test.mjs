import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("EB-02 reads the Windows Chromium version from PE metadata", async () => {
  const source = await readFile(
    new URL("../../scripts/validate_shared_chromium.py", import.meta.url),
    "utf8",
  );

  assert.match(source, /platform\.system\(\) == "Windows"/u);
  assert.match(source, /GetFileVersionInfoSizeW/u);
  assert.match(source, /GetFileVersionInfoW/u);
  assert.match(source, /VerQueryValueW/u);
  assert.match(source, /dwProductVersionMS/u);
  assert.match(source, /dwProductVersionLS/u);
  assert.match(source, /without_proxy_environment/u);
  assert.match(source, /all_proxy/ui);
  assert.match(source, /session: BrowserSession \| None = None/u);
});
