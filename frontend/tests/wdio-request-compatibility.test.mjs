import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { withoutExplicitContentLength } from "../wdio-request-compatibility.ts";

test("WDIO requests let Undici calculate Content-Length on Node 26", () => {
  const headers = new Headers({
    Accept: "application/json",
    "Content-Length": "123",
  });
  const request = { body: "{}", headers, method: "POST" };

  assert.equal(withoutExplicitContentLength(request), request);
  assert.equal(headers.get("content-length"), null);
  assert.equal(headers.get("accept"), "application/json");
});

test("every WDIO run applies the Node 26 request compatibility boundary", async () => {
  const [source, nodeProject] = await Promise.all([
    readFile(new URL("../wdio-runtime-artifacts.ts", import.meta.url), "utf8"),
    readFile(new URL("../tsconfig.node.json", import.meta.url), "utf8"),
  ]);

  assert.match(source, /withoutExplicitContentLength/u);
  assert.match(source, /transformRequest:\s*withoutExplicitContentLength/u);
  assert.match(nodeProject, /"wdio-request-compatibility\.ts"/u);
});
