import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  ExecutorProtocolError,
  parseExecutorMessage,
} from "./executor-envelope";

const fixtureRoot = resolve(process.cwd(), "../contracts/fixtures/executor-v1");
const validRoot = resolve(fixtureRoot, "valid");
const invalidRoot = resolve(fixtureRoot, "invalid");
const expectedValid = [
  "action-accept.json",
  "action-execute.json",
  "executor-heartbeat.json",
  "executor-hello.json",
  "microsecond-deadline.json",
  "platform-session-health.json",
  "step-progress.json",
  "task-accept.json",
  "task-discover.json",
  "task-discovery-batch.json",
  "task-discovery-completed.json",
  "task-offer.json",
];
const expectedInvalid = [
  "deadline-before-send-microsecond.json",
  "deadline-before-send.json",
  "deadline-equals-send.json",
  "discovery-command-unknown-field.json",
  "duplicate-key.json",
  "inline-data-uri.json",
  "invalid-idempotency-key.json",
  "invalid-message-id.json",
  "invalid-sequence-type.json",
  "invalid-sequence-zero.json",
  "invalid-version.json",
  "lifecycle-with-task-scope.json",
  "missing-protocol-version.json",
  "naive-sent-at.json",
  "negative-zero-offset.json",
  "non-finite-number.json",
  "non-utc-sent-at.json",
  "payload-too-deep.json",
  "payload-too-many-fields.json",
  "platform-session-health-with-task-scope.json",
  "private-path.json",
  "sensitive-assignment.json",
  "sensitive-cookie-field.json",
  "task-missing-attempt.json",
  "unknown-envelope-field.json",
  "unknown-message-type.json",
  "unsafe-sequence.json",
];

function fixtureNames(root: string): string[] {
  return readdirSync(root).filter((name) => name.endsWith(".json")).sort();
}

describe("Executor v1 shared fixtures", () => {
  it("keeps the TypeScript inventory on the exact shared fixture set", () => {
    expect(fixtureNames(validRoot)).toEqual(expectedValid);
    expect(fixtureNames(invalidRoot)).toEqual(expectedInvalid);
  });

  for (const fixtureName of expectedValid) {
    it(`accepts valid/${fixtureName} through the formal TypeScript parser`, () => {
      const raw = readFileSync(resolve(validRoot, fixtureName), "utf8");
      const parsed = parseExecutorMessage(raw);

      expect(parsed.protocol_version).toBe("1.0");
      expect(parseExecutorMessage(JSON.stringify(parsed))).toEqual(parsed);
    });
  }

  for (const fixtureName of expectedInvalid) {
    it(`rejects invalid/${fixtureName} with one safe TypeScript error`, () => {
      const raw = readFileSync(resolve(invalidRoot, fixtureName), "utf8");

      let captured: unknown;
      try {
        parseExecutorMessage(raw);
      } catch (error) {
        captured = error;
      }

      expect(captured).toBeInstanceOf(ExecutorProtocolError);
      expect(captured).toMatchObject({
        name: "ExecutorProtocolError",
        message: "Invalid Executor protocol message",
      });
      expect(captured).not.toHaveProperty("cause");
    });
  }
});
