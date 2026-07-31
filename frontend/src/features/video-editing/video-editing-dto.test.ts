import { describe, expect, it } from "vitest";

import {
  editingCaptionStyleSchema,
  editingJobSchema,
  editingOutputSpecSchema,
  editingProjectSchema,
  editingTimelineDraftSchema,
  editingTimelineSchema,
  originalAudioModeSchema,
  timelineClipSchema,
  timelineTrackKindSchema,
  timelineTrackSchema,
  transitionKindSchema,
} from "./video-editing-dto";

const PROJECT_ID = "0a48954d-2df1-4168-8f33-b62c5772845a";
const TIMELINE_ID = "1b70168c-90d0-4ac7-938a-51eb4754f32a";
const JOB_ID = "3d594650-b5f4-4498-8e38-0cf85d6dfa72";
const MATERIAL_A = "9f48954d-2df1-4168-8f33-b62c5772845b";
const ARTIFACT_A = "8e48954d-2df1-4168-8f33-b62c5772845c";
const CREATED_AT = "2026-07-23T00:00:00.000Z";

function sourceClip(overrides: Record<string, unknown> = {}) {
  return {
    clipId: "clip-1",
    startMs: 0,
    durationMs: 3_000,
    sourceMaterialId: MATERIAL_A,
    sourceInMs: 0,
    sourceOutMs: 3_000,
    text: null,
    gainDb: null,
    transitionIn: null,
    originalAudioMode: null,
    ...overrides,
  };
}

function captionClip(overrides: Record<string, unknown> = {}) {
  return {
    clipId: "caption-1",
    startMs: 0,
    durationMs: 3_000,
    sourceMaterialId: null,
    sourceInMs: null,
    sourceOutMs: null,
    text: "第一句字幕",
    gainDb: null,
    transitionIn: null,
    originalAudioMode: null,
    ...overrides,
  };
}

function visualTrack(overrides: Record<string, unknown> = {}) {
  return { trackId: "track-visual", kind: "visual", clips: [sourceClip()], ...overrides };
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

function project(overrides: Record<string, unknown> = {}) {
  return {
    projectId: PROJECT_ID,
    title: "发布会剪辑",
    output: { width: 720, height: 1280, fps: 20 },
    captionStyle: {
      fontKey: "noto-sans-cjk-sc",
      fontPx: 48,
      strokePx: 3,
      lineSpacing: 1.2,
    },
    createdAt: CREATED_AT,
    ...overrides,
  };
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    jobId: JOB_ID,
    projectId: PROJECT_ID,
    timelineId: TIMELINE_ID,
    timelineRevision: 2,
    status: "queued",
    failureCode: null,
    outputArtifactId: null,
    createdAt: CREATED_AT,
    updatedAt: CREATED_AT,
    ...overrides,
  };
}

describe("current Control Plane video editing DTO schemas", () => {
  it("accepts exact current project, timeline and job snapshots", () => {
    expect(editingProjectSchema.safeParse(project()).success).toBe(true);
    expect(editingTimelineSchema.safeParse(timeline()).success).toBe(true);
    expect(editingJobSchema.safeParse(job()).success).toBe(true);
  });

  it("rejects every retired LE-01 field instead of keeping a compatibility shape", () => {
    expect(
      editingProjectSchema.safeParse({
        ...project(),
        sourceArtifactIds: [ARTIFACT_A],
        updatedAt: CREATED_AT,
      }).success,
    ).toBe(false);
    expect(
      timelineClipSchema.safeParse({
        ...sourceClip(),
        sourceMaterialId: undefined,
        sourceArtifactId: ARTIFACT_A,
      }).success,
    ).toBe(false);
    expect(
      editingJobSchema.safeParse({
        ...job(),
        jobId: undefined,
        editingJobId: JOB_ID,
        inputArtifactIds: [ARTIFACT_A],
        outputArtifactIds: [],
      }).success,
    ).toBe(false);
    expect(editingJobSchema.safeParse({ ...job(), status: "paused" }).success).toBe(false);
    expect(
      editingJobSchema.safeParse({ ...job(), status: "outcome_uncertain" }).success,
    ).toBe(false);
  });

  it("uses absolute string boundaries and rejects every Unicode Other category", () => {
    expect(
      timelineClipSchema.safeParse(sourceClip({ clipId: "clip-1\n" })).success,
    ).toBe(false);
    expect(
      timelineClipSchema.safeParse({
        ...sourceClip(),
        sourceMaterialId: `${MATERIAL_A}\n`,
      }).success,
    ).toBe(false);
    expect(
      editingCaptionStyleSchema.safeParse({
        fontKey: "font-key\n",
        fontPx: 48,
        strokePx: 3,
        lineSpacing: 1.2,
      }).success,
    ).toBe(false);
    expect(editingProjectSchema.safeParse(project({ title: "私有\uE000字" })).success).toBe(
      false,
    );
    expect(
      timelineClipSchema.safeParse(captionClip({ text: "字幕\uE000" })).success,
    ).toBe(false);
  });

  it("keeps the exact five-lane, three-transition and original-audio vocabularies", () => {
    expect(timelineTrackKindSchema.options).toEqual([
      "visual",
      "narration",
      "ambient",
      "music",
      "caption",
    ]);
    expect(transitionKindSchema.options).toEqual(["fade", "dissolve", "wipe"]);
    expect(originalAudioModeSchema.options).toEqual([
      "auto_duck",
      "fixed_volume",
      "muted",
    ]);
  });

  it("enforces output and caption facts that belong to the project", () => {
    expect(editingOutputSpecSchema.safeParse({ width: 720, height: 1280, fps: 20 }).success).toBe(
      true,
    );
    expect(editingOutputSpecSchema.safeParse({ width: 721, height: 1280, fps: 20 }).success).toBe(
      false,
    );
    expect(editingOutputSpecSchema.safeParse({ width: 720, height: 1280, fps: 61 }).success).toBe(
      false,
    );
    expect(
      editingCaptionStyleSchema.safeParse({
        fontKey: "../private-font",
        fontPx: 48,
        strokePx: 3,
        lineSpacing: 1.2,
      }).success,
    ).toBe(false);
    expect(
      editingProjectSchema.safeParse(
        project({
          output: { width: 720, height: 128, fps: 20 },
          captionStyle: {
            fontKey: "noto-sans-cjk-sc",
            fontPx: 129,
            strokePx: 3,
            lineSpacing: 1.2,
          },
        }),
      ).success,
    ).toBe(false);
  });

  it("accepts a source window and rejects incomplete or stretched source slices", () => {
    expect(timelineClipSchema.safeParse(sourceClip()).success).toBe(true);
    expect(timelineClipSchema.safeParse(sourceClip({ sourceOutMs: null })).success).toBe(false);
    expect(timelineClipSchema.safeParse(sourceClip({ sourceOutMs: 2_999 })).success).toBe(false);
    expect(
      timelineClipSchema.safeParse(
        captionClip({ sourceInMs: 0, sourceOutMs: 3_000 }),
      ).success,
    ).toBe(false);
  });

  it("enforces clip shape independently for all five track kinds", () => {
    const narration = {
      trackId: "track-narration",
      kind: "narration",
      clips: [sourceClip({ gainDb: -3.5 })],
    };
    const ambient = {
      trackId: "track-ambient",
      kind: "ambient",
      clips: [sourceClip({ gainDb: -6.5, originalAudioMode: "auto_duck" })],
    };
    const music = {
      trackId: "track-music",
      kind: "music",
      clips: [sourceClip({ gainDb: -12.5 })],
    };
    expect(timelineTrackSchema.safeParse(narration).success).toBe(true);
    expect(timelineTrackSchema.safeParse(ambient).success).toBe(true);
    expect(timelineTrackSchema.safeParse(music).success).toBe(true);
    expect(
      timelineTrackSchema.safeParse({ ...narration, clips: [sourceClip()] }).success,
    ).toBe(false);
    expect(
      timelineTrackSchema.safeParse({
        ...ambient,
        clips: [sourceClip({ gainDb: -6.5, originalAudioMode: null })],
      }).success,
    ).toBe(false);
    expect(
      timelineTrackSchema.safeParse({
        trackId: "track-caption",
        kind: "caption",
        clips: [captionClip({ transitionIn: { kind: "fade", durationMs: 100 } })],
      }).success,
    ).toBe(false);
  });

  it("allows exact visual transition overlap but no other overlap", () => {
    const transitionClips = [
      sourceClip({ durationMs: 2_000, sourceOutMs: 2_000 }),
      sourceClip({
        clipId: "clip-2",
        startMs: 1_500,
        durationMs: 1_500,
        sourceInMs: 2_000,
        sourceOutMs: 3_500,
        transitionIn: { kind: "dissolve", durationMs: 500 },
      }),
    ];
    expect(
      editingTimelineSchema.safeParse(
        timeline({ tracks: [visualTrack({ clips: transitionClips })] }),
      ).success,
    ).toBe(true);
    expect(
      editingTimelineSchema.safeParse(
        timeline({
          tracks: [
            visualTrack({
              clips: [transitionClips[0], { ...transitionClips[1], startMs: 2_000 }],
            }),
          ],
        }),
      ).success,
    ).toBe(false);
    expect(
      timelineTrackSchema.safeParse({
        trackId: "track-music",
        kind: "music",
        clips: [
          sourceClip({ durationMs: 2_000, sourceOutMs: 2_000, gainDb: -3.5 }),
          sourceClip({
            clipId: "clip-2",
            startMs: 1_500,
            durationMs: 1_500,
            sourceInMs: 2_000,
            sourceOutMs: 3_500,
            gainDb: -3.5,
          }),
        ],
      }).success,
    ).toBe(false);
  });

  it("requires one visual lane ending at duration and at most one lane of each kind", () => {
    expect(
      editingTimelineSchema.safeParse(
        timeline({
          tracks: [{ trackId: "track-caption", kind: "caption", clips: [captionClip()] }],
        }),
      ).success,
    ).toBe(false);
    expect(
      editingTimelineSchema.safeParse(
        timeline({ tracks: [visualTrack(), visualTrack({ trackId: "visual-two" })] }),
      ).success,
    ).toBe(false);
    expect(editingTimelineSchema.safeParse(timeline({ durationMs: 3_001 })).success).toBe(false);
  });

  it("enforces the six-state job terminal facts and all current failure codes", () => {
    expect(
      editingJobSchema.safeParse(
        job({ status: "succeeded", outputArtifactId: ARTIFACT_A }),
      ).success,
    ).toBe(true);
    for (const failureCode of [
      "invalid_timeline",
      "material_unavailable",
      "material_unsupported",
      "font_unavailable",
      "render_failed",
      "resource_exhausted",
      "permission_denied",
      "worker_lost",
    ]) {
      expect(
        editingJobSchema.safeParse(job({ status: "failed", failureCode })).success,
      ).toBe(true);
    }
    expect(
      editingJobSchema.safeParse(job({ status: "failed", failureCode: null })).success,
    ).toBe(false);
    expect(
      editingJobSchema.safeParse(
        job({ status: "running", outputArtifactId: ARTIFACT_A }),
      ).success,
    ).toBe(false);
  });

  it("validates save drafts with the same timeline structure", () => {
    expect(
      editingTimelineDraftSchema.safeParse({
        durationMs: 3_000,
        tracks: [visualTrack()],
      }).success,
    ).toBe(true);
    expect(
      editingTimelineDraftSchema.safeParse({
        durationMs: 3_001,
        tracks: [visualTrack()],
      }).success,
    ).toBe(false);
  });
});
