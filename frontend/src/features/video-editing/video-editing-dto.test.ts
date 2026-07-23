import { describe, expect, it } from "vitest";

import {
  editingJobSchema,
  editingProjectSchema,
  editingTimelineDraftSchema,
  editingTimelineSchema,
  timelineClipSchema,
  timelineTrackSchema,
  timelineTransitionSchema,
} from "./video-editing-dto";

const PROJECT_ID = "0a48954d-2df1-4168-8f33-b62c5772845a";
const TIMELINE_ID = "1b70168c-90d0-4ac7-938a-51eb4754f32a";
const JOB_ID = "3d594650-b5f4-4498-8e38-0cf85d6dfa72";
const ARTIFACT_A = "9f48954d-2df1-4168-8f33-b62c5772845b";
const ARTIFACT_B = "8e48954d-2df1-4168-8f33-b62c5772845c";
const CREATED_AT = "2026-07-23T00:00:00.000Z";

function visualClip(overrides: Record<string, unknown> = {}) {
  return {
    clipId: "clip-1",
    startMs: 0,
    durationMs: 3_000,
    sourceArtifactId: ARTIFACT_A,
    text: null,
    transitionIn: null,
    ...overrides,
  };
}

function captionClip(overrides: Record<string, unknown> = {}) {
  return {
    clipId: "caption-1",
    startMs: 0,
    durationMs: 3_000,
    sourceArtifactId: null,
    text: "第一句字幕",
    transitionIn: null,
    ...overrides,
  };
}

function visualTrack(overrides: Record<string, unknown> = {}) {
  return { trackId: "track-visual", kind: "visual", clips: [visualClip()], ...overrides };
}

function timeline(overrides: Record<string, unknown> = {}) {
  return {
    timelineId: TIMELINE_ID,
    projectId: PROJECT_ID,
    revision: 1,
    durationMs: 3_000,
    tracks: [visualTrack()],
    createdAt: CREATED_AT,
    ...overrides,
  };
}

describe("video editing DTO schemas", () => {
  it("accepts a valid project, timeline, and job snapshot", () => {
    expect(
      editingProjectSchema.safeParse({
        projectId: PROJECT_ID,
        title: "发布会剪辑",
        sourceArtifactIds: [ARTIFACT_A, ARTIFACT_B],
        createdAt: CREATED_AT,
        updatedAt: CREATED_AT,
      }).success,
    ).toBe(true);

    expect(editingTimelineSchema.safeParse(timeline()).success).toBe(true);

    expect(
      editingJobSchema.safeParse({
        editingJobId: JOB_ID,
        projectId: PROJECT_ID,
        timelineId: TIMELINE_ID,
        timelineRevision: 2,
        status: "queued",
        inputArtifactIds: [ARTIFACT_A],
        outputArtifactIds: [],
        failureCode: null,
        createdAt: CREATED_AT,
        updatedAt: CREATED_AT,
      }).success,
    ).toBe(true);
  });

  it("keeps exactly the provider-neutral domain fields and nothing else", () => {
    expect(Object.keys(editingProjectSchema.shape).sort()).toEqual([
      "createdAt",
      "projectId",
      "sourceArtifactIds",
      "title",
      "updatedAt",
    ]);
    expect(Object.keys(editingTimelineSchema.shape).sort()).toEqual([
      "createdAt",
      "durationMs",
      "projectId",
      "revision",
      "timelineId",
      "tracks",
    ]);
    expect(Object.keys(timelineTrackSchema.shape).sort()).toEqual([
      "clips",
      "kind",
      "trackId",
    ]);
    expect(Object.keys(timelineClipSchema.shape).sort()).toEqual([
      "clipId",
      "durationMs",
      "sourceArtifactId",
      "startMs",
      "text",
      "transitionIn",
    ]);
    expect(Object.keys(timelineTransitionSchema.shape).sort()).toEqual([
      "durationMs",
      "kind",
    ]);
    expect(Object.keys(editingJobSchema.shape).sort()).toEqual([
      "createdAt",
      "editingJobId",
      "failureCode",
      "inputArtifactIds",
      "outputArtifactIds",
      "projectId",
      "status",
      "timelineId",
      "timelineRevision",
      "updatedAt",
    ]);

    const allKeys = [
      ...Object.keys(editingProjectSchema.shape),
      ...Object.keys(editingTimelineSchema.shape),
      ...Object.keys(timelineTrackSchema.shape),
      ...Object.keys(timelineClipSchema.shape),
      ...Object.keys(timelineTransitionSchema.shape),
      ...Object.keys(editingJobSchema.shape),
    ];
    for (const key of allKeys) {
      expect(key).not.toMatch(
        /provider|vendor|region|api.?key|access.?key|secret|endpoint|aliyun|tencent|ims|ice|oss/i,
      );
    }
  });

  it("rejects unknown extra fields such as vendor payloads", () => {
    expect(
      editingTimelineSchema.safeParse({ ...timeline(), providerJobId: "x" }).success,
    ).toBe(false);
    expect(
      editingProjectSchema.safeParse({
        projectId: PROJECT_ID,
        title: "发布会剪辑",
        sourceArtifactIds: [],
        createdAt: CREATED_AT,
        updatedAt: CREATED_AT,
        region: "cn-shanghai",
      }).success,
    ).toBe(false);
  });

  it("rejects invalid projects", () => {
    const base = {
      projectId: PROJECT_ID,
      title: "发布会剪辑",
      sourceArtifactIds: [ARTIFACT_A],
      createdAt: CREATED_AT,
      updatedAt: CREATED_AT,
    };
    expect(editingProjectSchema.safeParse({ ...base, title: "" }).success).toBe(false);
    expect(editingProjectSchema.safeParse({ ...base, title: "  空白  " }).success).toBe(false);
    expect(
      editingProjectSchema.safeParse({ ...base, title: "长".repeat(201) }).success,
    ).toBe(false);
    expect(
      editingProjectSchema.safeParse({ ...base, projectId: "not-a-uuid" }).success,
    ).toBe(false);
    expect(
      editingProjectSchema.safeParse({
        ...base,
        sourceArtifactIds: [ARTIFACT_A, ARTIFACT_A],
      }).success,
    ).toBe(false);
  });

  it("rejects invalid timeline structure", () => {
    expect(editingTimelineSchema.safeParse(timeline({ revision: 0 })).success).toBe(false);
    expect(editingTimelineSchema.safeParse(timeline({ durationMs: 99 })).success).toBe(false);
    expect(
      editingTimelineSchema.safeParse(timeline({ durationMs: 600_001 })).success,
    ).toBe(false);
    expect(editingTimelineSchema.safeParse(timeline({ tracks: [] })).success).toBe(false);
    expect(
      editingTimelineSchema.safeParse(
        timeline({
          tracks: [
            { trackId: "caption-track", kind: "caption", clips: [captionClip()] },
          ],
        }),
      ).success,
    ).toBe(false);
    expect(
      editingTimelineSchema.safeParse(
        timeline({ tracks: [visualTrack(), visualTrack()] }),
      ).success,
    ).toBe(false);
    expect(
      editingTimelineSchema.safeParse(
        timeline({
          durationMs: 1_000,
          tracks: [visualTrack({ clips: [visualClip({ durationMs: 2_000 })] })],
        }),
      ).success,
    ).toBe(false);
  });

  it("rejects invalid tracks and clips", () => {
    expect(
      timelineTrackSchema.safeParse(visualTrack({ trackId: "Bad_Id" })).success,
    ).toBe(false);
    expect(timelineTrackSchema.safeParse(visualTrack({ clips: [] })).success).toBe(false);
    expect(
      timelineTrackSchema.safeParse(
        visualTrack({ clips: [visualClip(), visualClip()] }),
      ).success,
    ).toBe(false);
    expect(
      timelineTrackSchema.safeParse(
        visualTrack({
          clips: [
            visualClip(),
            visualClip({ clipId: "clip-2", startMs: 1_000 }),
          ],
        }),
      ).success,
    ).toBe(false);
    expect(
      timelineTrackSchema.safeParse(
        visualTrack({ clips: [visualClip({ sourceArtifactId: null, text: "文字" })] }),
      ).success,
    ).toBe(false);
    expect(
      timelineTrackSchema.safeParse({
        trackId: "caption-track",
        kind: "caption",
        clips: [captionClip({ sourceArtifactId: ARTIFACT_A, text: null })],
      }).success,
    ).toBe(false);
    expect(
      timelineClipSchema.safeParse(
        visualClip({ sourceArtifactId: null, text: null }),
      ).success,
    ).toBe(false);
    expect(timelineClipSchema.safeParse(visualClip({ durationMs: 0 })).success).toBe(false);
    expect(timelineClipSchema.safeParse(visualClip({ startMs: -1 })).success).toBe(false);
  });

  it("rejects invalid transitions", () => {
    expect(
      timelineTransitionSchema.safeParse({ kind: "fade", durationMs: 500 }).success,
    ).toBe(true);
    expect(
      timelineTransitionSchema.safeParse({ kind: "fade", durationMs: 0 }).success,
    ).toBe(false);
    expect(
      timelineTransitionSchema.safeParse({ kind: "fade", durationMs: 10_001 }).success,
    ).toBe(false);
    expect(
      timelineTransitionSchema.safeParse({ kind: "zoom", durationMs: 500 }).success,
    ).toBe(false);
  });

  it("enforces editing job status facts like the domain state machine", () => {
    const base = {
      editingJobId: JOB_ID,
      projectId: PROJECT_ID,
      timelineId: TIMELINE_ID,
      timelineRevision: 1,
      status: "succeeded",
      inputArtifactIds: [ARTIFACT_A],
      outputArtifactIds: [ARTIFACT_B],
      failureCode: null,
      createdAt: CREATED_AT,
      updatedAt: CREATED_AT,
    };
    expect(editingJobSchema.safeParse(base).success).toBe(true);
    expect(
      editingJobSchema.safeParse({ ...base, outputArtifactIds: [] }).success,
    ).toBe(false);
    expect(
      editingJobSchema.safeParse({
        ...base,
        status: "failed",
        outputArtifactIds: [],
        failureCode: "editing_failed",
      }).success,
    ).toBe(true);
    expect(
      editingJobSchema.safeParse({
        ...base,
        status: "failed",
        outputArtifactIds: [ARTIFACT_B],
        failureCode: "editing_failed",
      }).success,
    ).toBe(false);
    expect(
      editingJobSchema.safeParse({
        ...base,
        status: "running",
        outputArtifactIds: [],
        failureCode: "editing_failed",
      }).success,
    ).toBe(false);
    expect(
      editingJobSchema.safeParse({
        ...base,
        status: "running",
        outputArtifactIds: [ARTIFACT_A],
        failureCode: null,
      }).success,
    ).toBe(false);
    expect(
      editingJobSchema.safeParse({ ...base, inputArtifactIds: [] }).success,
    ).toBe(false);
    expect(
      editingJobSchema.safeParse({
        ...base,
        status: "queued",
        outputArtifactIds: [],
        inputArtifactIds: [ARTIFACT_A, ARTIFACT_A],
      }).success,
    ).toBe(false);
  });

  it("validates drafts with the same structural rules before save", () => {
    expect(
      editingTimelineDraftSchema.safeParse({
        durationMs: 3_000,
        tracks: [visualTrack()],
      }).success,
    ).toBe(true);
    expect(
      editingTimelineDraftSchema.safeParse({ durationMs: 3_000, tracks: [] }).success,
    ).toBe(false);
    expect(
      editingTimelineDraftSchema.safeParse({
        durationMs: 2_000,
        tracks: [visualTrack({ clips: [visualClip({ durationMs: 3_000 })] })],
      }).success,
    ).toBe(false);
  });
});
