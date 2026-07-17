import assert from "node:assert/strict";
import test from "node:test";

import { normalizeGeneratedText } from "../scripts/generated-text.mjs";

test("generated source comparison is stable across LF and Windows CRLF", () => {
  const expected = "export interface Health {\n  status: string;\n}\n";

  assert.equal(normalizeGeneratedText(expected), expected);
  assert.equal(
    normalizeGeneratedText("export interface Health {\r\n  status: string;\r\n}\r\n"),
    expected,
  );
  assert.equal(
    normalizeGeneratedText("export interface Health {\r  status: string;\r}\r"),
    expected,
  );
});
