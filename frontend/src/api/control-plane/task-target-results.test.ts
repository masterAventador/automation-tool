import { describe, expect, it } from "vitest";

import {
  TaskTargetResultSourceError,
  parseTaskTargetResultSnapshot,
} from "./task-target-results";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const TARGET_ID = "16fd2706-8baf-433b-82eb-8c7fada847da";
const ACTION_ID = "adff54bd-3571-44da-8acd-5ea15695e5e9";

const snapshot = {
  taskId: TASK_ID,
  taskStatus: "partially_succeeded",
  taskRevision: 8,
  lastEventSequence: 7,
  items: [
    {
      targetId: TARGET_ID,
      ordinal: 1,
      displayName: "目标一",
      publicHandle: "target.one",
      resultStatus: "succeeded",
      evidence: "comment_confirmed",
      actionId: ACTION_ID,
      updatedAt: "2026-07-21T08:00:00Z",
    },
  ],
} as const;

describe("Task target result contract", () => {
  it("parses one exact privacy-safe result snapshot", () => {
    expect(parseTaskTargetResultSnapshot(snapshot)).toEqual(snapshot);
  });

  it.each([
    { ...snapshot, privatePath: "/Users/private" },
    {
      ...snapshot,
      items: [{ ...snapshot.items[0], evidence: "user_excluded" }],
    },
    {
      ...snapshot,
      items: [{ ...snapshot.items[0], actionId: null }],
    },
    {
      ...snapshot,
      items: [snapshot.items[0], { ...snapshot.items[0], ordinal: 2 }],
    },
    {
      ...snapshot,
      items: [{ ...snapshot.items[0], displayName: "password=private" }],
    },
  ])("rejects malformed, incoherent, duplicate, or sensitive state", (value) => {
    expect(() => parseTaskTargetResultSnapshot(value)).toThrow(TaskTargetResultSourceError);
  });
});
