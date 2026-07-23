#!/usr/bin/env node
/** Isolated process boundary for the motion-composition runtime. */

import { createHmac, timingSafeEqual } from "node:crypto";
import { lstat, realpath } from "node:fs/promises";
import { createServer } from "node:http";
import { isAbsolute } from "node:path";
import { createInterface } from "node:readline";

const WORKER_VERSION = "0.7.68";
const PROTOCOL_VERSION = "1.0";
const BOOTSTRAP_VERSION = "1";
const MAX_LINE_BYTES = 16 * 1024;
const EVENT_DOMAIN = "automation-tool.video-worker-event.v1\0";
const COMMAND_DOMAIN = "automation-tool.video-worker-command.v1\0";
const TOKEN_PATTERN = /^[0-9a-f]{64}$/;
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const FIXED_BOOTSTRAP_ERROR = "Motion composition worker bootstrap is rejected";

function fixedJson(value) {
  return JSON.stringify(value);
}

function hasExactKeys(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

async function parseBootstrap(line) {
  if (Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) throw new Error("rejected");
  let value;
  try {
    value = JSON.parse(line);
  } catch {
    throw new Error("rejected");
  }
  if (!hasExactKeys(value, [
    "assetRoot", "bootstrapVersion", "enableWebUi", "localSessionToken",
    "protocolVersion", "scriptModel", "workerKind",
  ])) throw new Error("rejected");
  if (value.bootstrapVersion !== BOOTSTRAP_VERSION
      || value.protocolVersion !== PROTOCOL_VERSION
      || value.workerKind !== "node"
      || value.enableWebUi !== false
      || value.scriptModel !== null
      || typeof value.assetRoot !== "string"
      || !isAbsolute(value.assetRoot)
      || typeof value.localSessionToken !== "string"
      || !TOKEN_PATTERN.test(value.localSessionToken)) throw new Error("rejected");
  const metadata = await lstat(value.assetRoot);
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error("rejected");
  value.assetRoot = await realpath(value.assetRoot);
  return value;
}

function proof(token, domain, parts, prefix) {
  const mac = createHmac("sha256", Buffer.from(token, "hex"));
  mac.update(domain);
  mac.update(parts.join("\0"));
  return prefix + mac.digest("base64url");
}

function eventProof(bootstrap, event, detail) {
  return proof(
    bootstrap.localSessionToken,
    EVENT_DOMAIN,
    [event, "node", PROTOCOL_VERSION, WORKER_VERSION, detail],
    "atvwp1.",
  );
}

function validCancel(bootstrap, command) {
  if (!hasExactKeys(command, [
    "authenticationProof", "command", "jobId", "protocolVersion", "workerKind",
  ])) return false;
  if (command.command !== "worker.cancel"
      || command.protocolVersion !== PROTOCOL_VERSION
      || command.workerKind !== "node"
      || typeof command.jobId !== "string"
      || !UUID_V4_PATTERN.test(command.jobId)) return false;
  const expected = proof(
    bootstrap.localSessionToken,
    COMMAND_DOMAIN,
    ["worker.cancel", "node", PROTOCOL_VERSION, command.jobId],
    "atvwc1.",
  );
  if (typeof command.authenticationProof !== "string") return false;
  const actualBytes = Buffer.from(command.authenticationProof);
  const expectedBytes = Buffer.from(expected);
  return actualBytes.length === expectedBytes.length && timingSafeEqual(actualBytes, expectedBytes);
}

async function main() {
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  const iterator = lines[Symbol.asyncIterator]();
  const first = await iterator.next();
  if (first.done) {
    process.stderr.write("Motion composition worker command is required\n");
    return 64;
  }
  let bootstrap;
  try {
    bootstrap = await parseBootstrap(first.value);
  } catch {
    process.stderr.write(`${FIXED_BOOTSTRAP_ERROR}\n`);
    return 65;
  }

  const server = createServer((request, response) => {
    const authorized = request.headers.authorization === `Bearer ${bootstrap.localSessionToken}`;
    if (request.method !== "GET" || request.url !== "/health" || !authorized) {
      response.writeHead(401, { "content-length": "0", connection: "close" });
      response.end();
      return;
    }
    const port = server.address().port;
    const body = fixedJson({
      authenticationProof: eventProof(bootstrap, "worker.health", String(port)),
      event: "worker.health",
      protocolVersion: PROTOCOL_VERSION,
      workerKind: "node",
      workerVersion: WORKER_VERSION,
      port,
    });
    response.writeHead(200, {
      "content-type": "application/json",
      "content-length": String(Buffer.byteLength(body)),
      connection: "close",
    });
    response.end(body);
  });
  server.on("clientError", (_error, socket) => socket.destroy());
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  process.stdout.write(`${fixedJson({
    authenticationProof: eventProof(bootstrap, "worker.ready", String(port)),
    event: "worker.ready",
    protocolVersion: PROTOCOL_VERSION,
    scriptModelId: null,
    webUiAuthenticationProof: null,
    webUiPath: null,
    webUiPort: null,
    workerKind: "node",
    workerVersion: WORKER_VERSION,
    port,
  })}\n`);

  for await (const line of { [Symbol.asyncIterator]: () => iterator }) {
    if (Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) continue;
    let command;
    try {
      command = JSON.parse(line);
    } catch {
      continue;
    }
    if (!validCancel(bootstrap, command)) continue;
    process.stdout.write(`${fixedJson({
      authenticationProof: eventProof(bootstrap, "worker.cancelled", command.jobId),
      event: "worker.cancelled",
      jobId: command.jobId,
      protocolVersion: PROTOCOL_VERSION,
      workerKind: "node",
      workerVersion: WORKER_VERSION,
    })}\n`);
  }
  await new Promise((resolve) => server.close(resolve));
  return 0;
}

main().then((code) => { process.exitCode = code; }).catch(() => {
  process.stderr.write(`${FIXED_BOOTSTRAP_ERROR}\n`);
  process.exitCode = 70;
});
