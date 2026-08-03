import { describe, expect, it } from "vitest";

import {
  smartEditGenerationRequestSchema,
  smartEditGenerationSnapshotSchema,
} from "./smart-edit-gateway";

const PROJECT_ID = "0a48954d-2df1-4168-8f33-b62c5772845a";
const GENERATION_ID = "3d594650-b5f4-4498-8e38-0cf85d6dfa72";
const TIMELINE_ID = "1b70168c-90d0-4ac7-938a-51eb4754f32a";
const MATERIAL_ID = "9f48954d-2df1-4168-8f33-b62c5772845b";

function runningSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    generationId: GENERATION_ID,
    projectId: PROJECT_ID,
    mode: "draft",
    status: "running",
    stage: "analyzing",
    progressPermille: 250,
    timeline: null,
    renderJob: null,
    failureCode: null,
    ...overrides,
  };
}

function timeline() {
  return {
    timelineId: TIMELINE_ID,
    projectId: PROJECT_ID,
    revision: 1,
    durationMs: 1_000,
    tracks: [
      {
        trackId: "visual",
        kind: "visual",
        clips: [
          {
            clipId: "visual-0001",
            startMs: 0,
            durationMs: 1_000,
            sourceMaterialId: MATERIAL_ID,
            sourceInMs: 0,
            sourceOutMs: 1_000,
            text: null,
            gainDb: null,
            transitionIn: null,
            originalAudioMode: null,
          },
        ],
      },
    ],
    createdAt: "2026-08-01T00:00:00Z",
  };
}

function renderJob(overrides: Record<string, unknown> = {}) {
  return {
    jobId: "4d594650-b5f4-4498-8e38-0cf85d6dfa73",
    projectId: PROJECT_ID,
    timelineId: TIMELINE_ID,
    timelineRevision: 1,
    status: "queued",
    failureCode: null,
    outputArtifactId: null,
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

describe("smart-edit gateway DTO boundary", () => {
  it("accepts one exact path-free request and running snapshot", () => {
    expect(
      smartEditGenerationRequestSchema.safeParse({
        projectId: PROJECT_ID,
        prompt: "把发布会开场剪成一条节奏明快的短片",
        enableThinking: false,
        mode: "draft",
      }).success,
    ).toBe(true);
    expect(smartEditGenerationSnapshotSchema.safeParse(runningSnapshot()).success).toBe(
      true,
    );
  });

  it("rejects protocol spelling drift, private fields and incoherent terminal facts", () => {
    for (const invalid of [
      { ...runningSnapshot(), progressPermille: undefined, progressPerMille: 250 },
      { ...runningSnapshot(), privatePath: "/private/result.json" },
      { ...runningSnapshot(), prompt: "不应回显" },
      { ...runningSnapshot(), stage: "model_call" },
      { ...runningSnapshot(), status: "succeeded", progressPermille: 1_000 },
      { ...runningSnapshot(), status: "failed", failureCode: null },
      { ...runningSnapshot(), status: "cancelled", failureCode: "local_failed" },
    ]) {
      expect(smartEditGenerationSnapshotSchema.safeParse(invalid).success).toBe(false);
    }
  });

  it("accepts only a render job bound to the exact saved timeline revision", () => {
    const rendered = runningSnapshot({
      mode: "render",
      status: "succeeded",
      stage: "completed",
      progressPermille: 1_000,
      timeline: timeline(),
      renderJob: renderJob(),
    });
    expect(smartEditGenerationSnapshotSchema.safeParse(rendered).success).toBe(true);
    expect(
      smartEditGenerationSnapshotSchema.safeParse({
        ...rendered,
        renderJob: renderJob({ timelineRevision: 2 }),
      }).success,
    ).toBe(false);
  });
});
