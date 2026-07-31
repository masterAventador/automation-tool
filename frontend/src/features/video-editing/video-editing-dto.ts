import { z } from "zod";

/**
 * Runtime DTO boundary for the standalone editing workbench.
 *
 * The generated OpenAPI types catch compile-time drift; these schemas reject
 * an untrusted native/HTTP response at runtime. Their fields and structural
 * rules mirror the current Control Plane editing domain rather than carrying
 * the retired cloud-editing shape as a compatibility model.
 */

export const MAX_EDITING_PROJECT_TITLE_CHARACTERS = 200;
export const MAX_VIDEO_DURATION_MS = 600_000;
export const MAX_MATERIAL_DURATION_MS = 14_400_000;
export const MIN_TIMELINE_DURATION_MS = 100;
export const MAX_TRACKS = 5;
export const MAX_CLIPS_PER_TRACK = 512;
export const MAX_TRANSITION_DURATION_MS = 10_000;

const CANONICAL_UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LOCAL_ID = /^[a-z][a-z0-9-]{0,63}$/;
const FONT_KEY = /^[a-z][a-z0-9-]{0,63}$/;

const resourceIdSchema = z.string().regex(CANONICAL_UUID_V4);
const localIdSchema = z.string().regex(LOCAL_ID);
const timestampSchema = z.iso.datetime();

const titleSchema = z
  .string()
  .min(1)
  .max(MAX_EDITING_PROJECT_TITLE_CHARACTERS)
  .refine((value) => value === value.trim())
  .refine((value) => !/\p{C}/u.test(value));

function uniqueValues(values: readonly string[]): boolean {
  return new Set(values).size === values.length;
}

export const editingOutputSpecSchema = z
  .strictObject({
    width: z.number().int().min(128).max(4_096),
    height: z.number().int().min(128).max(4_096),
    fps: z.number().int().min(12).max(60),
  })
  .refine((value) => value.width % 2 === 0 && value.height % 2 === 0);
export type EditingOutputSpec = z.infer<typeof editingOutputSpecSchema>;

export const editingCaptionStyleSchema = z
  .strictObject({
    fontKey: z.string().regex(FONT_KEY),
    fontPx: z.number().int().min(12).max(200),
    strokePx: z.number().int().min(0).max(20),
    lineSpacing: z.number().min(1).max(3),
  })
  .refine((value) => value.strokePx * 2 < value.fontPx);
export type EditingCaptionStyle = z.infer<typeof editingCaptionStyleSchema>;

export const editingProjectSchema = z
  .strictObject({
    projectId: resourceIdSchema,
    title: titleSchema,
    output: editingOutputSpecSchema,
    captionStyle: editingCaptionStyleSchema,
    createdAt: timestampSchema,
  })
  .refine((project) => project.captionStyle.fontPx <= project.output.height);
export type EditingProjectSnapshot = z.infer<typeof editingProjectSchema>;

export const timelineTrackKindSchema = z.enum([
  "visual",
  "narration",
  "ambient",
  "music",
  "caption",
]);
export type TimelineTrackKind = z.infer<typeof timelineTrackKindSchema>;

export const originalAudioModeSchema = z.enum([
  "auto_duck",
  "fixed_volume",
  "muted",
]);
export type OriginalAudioMode = z.infer<typeof originalAudioModeSchema>;

export const transitionKindSchema = z.enum(["fade", "dissolve", "wipe"]);
export type TransitionKind = z.infer<typeof transitionKindSchema>;

export const timelineTransitionSchema = z.strictObject({
  kind: transitionKindSchema,
  durationMs: z.number().int().min(1).max(MAX_TRANSITION_DURATION_MS),
});
export type TimelineTransitionSnapshot = z.infer<typeof timelineTransitionSchema>;

export const timelineClipSchema = z
  .strictObject({
    clipId: localIdSchema,
    startMs: z.number().int().min(0).max(MAX_VIDEO_DURATION_MS),
    durationMs: z.number().int().min(1).max(MAX_VIDEO_DURATION_MS),
    sourceMaterialId: resourceIdSchema.nullable(),
    sourceInMs: z.number().int().min(0).max(MAX_MATERIAL_DURATION_MS).nullable(),
    sourceOutMs: z.number().int().min(0).max(MAX_MATERIAL_DURATION_MS).nullable(),
    text: z
      .string()
      .min(1)
      .max(2_000)
      .refine((value) => value === value.trim())
      .refine((value) =>
        [...value].every(
          (character) =>
            character === "\n" || character === "\t" || !/\p{C}/u.test(character),
        ),
      )
      .nullable(),
    gainDb: z.number().min(-60).max(12).nullable(),
    transitionIn: timelineTransitionSchema.nullable(),
    originalAudioMode: originalAudioModeSchema.nullable(),
  })
  .superRefine((clip, context) => {
    const hasSource = clip.sourceMaterialId !== null;
    const hasText = clip.text !== null;
    const hasSourceIn = clip.sourceInMs !== null;
    const hasSourceOut = clip.sourceOutMs !== null;
    if (hasSource === hasText) {
      context.addIssue({ code: "custom", message: "clip content is invalid" });
    }
    if (hasSourceIn !== hasSourceOut || (hasSourceIn && !hasSource)) {
      context.addIssue({ code: "custom", message: "source window is invalid" });
    }
    if (
      clip.sourceInMs !== null &&
      clip.sourceOutMs !== null &&
      clip.sourceOutMs - clip.sourceInMs !== clip.durationMs
    ) {
      context.addIssue({ code: "custom", message: "source window duration is invalid" });
    }
    if (clip.gainDb !== null && !hasSourceIn) {
      context.addIssue({ code: "custom", message: "clip gain is invalid" });
    }
    if (clip.transitionIn !== null && clip.transitionIn.durationMs >= clip.durationMs) {
      context.addIssue({ code: "custom", message: "clip transition is invalid" });
    }
    if (clip.startMs + clip.durationMs > MAX_VIDEO_DURATION_MS) {
      context.addIssue({ code: "custom", message: "clip end is invalid" });
    }
  });
export type TimelineClipSnapshot = z.infer<typeof timelineClipSchema>;

function clipMatchesTrackKind(kind: TimelineTrackKind, clip: TimelineClipSnapshot): boolean {
  if (kind === "caption") {
    return (
      clip.text !== null &&
      clip.sourceMaterialId === null &&
      clip.gainDb === null &&
      clip.transitionIn === null &&
      clip.originalAudioMode === null
    );
  }
  if (clip.text !== null || clip.sourceMaterialId === null) {
    return false;
  }
  if (kind === "visual") {
    return clip.gainDb === null && clip.originalAudioMode === null;
  }
  if (clip.gainDb === null || clip.transitionIn !== null) {
    return false;
  }
  return kind === "ambient"
    ? clip.originalAudioMode !== null
    : clip.originalAudioMode === null;
}

function trackLayoutIsValid(track: {
  readonly kind: TimelineTrackKind;
  readonly clips: readonly TimelineClipSnapshot[];
}): boolean {
  let previousEnd = 0;
  let previousTail = 0;
  for (const clip of track.clips) {
    if (track.kind !== "visual") {
      if (clip.startMs < previousEnd) return false;
    } else {
      const overlap = clip.transitionIn?.durationMs ?? 0;
      if (clip.transitionIn !== null && overlap >= previousTail) return false;
      if (clip.startMs !== previousEnd - overlap) return false;
      previousTail = clip.durationMs - overlap;
    }
    previousEnd = clip.startMs + clip.durationMs;
  }
  return true;
}

export const timelineTrackSchema = z
  .strictObject({
    trackId: localIdSchema,
    kind: timelineTrackKindSchema,
    clips: z.array(timelineClipSchema).min(1).max(MAX_CLIPS_PER_TRACK),
  })
  .refine((track) => uniqueValues(track.clips.map((clip) => clip.clipId)))
  .refine((track) => track.clips.every((clip) => clipMatchesTrackKind(track.kind, clip)))
  .refine(trackLayoutIsValid);
export type TimelineTrackSnapshot = z.infer<typeof timelineTrackSchema>;

function timelineStructureIsValid(value: {
  readonly durationMs: number;
  readonly tracks: readonly TimelineTrackSnapshot[];
}): boolean {
  const visual = value.tracks.find((track) => track.kind === "visual");
  const visualEnd = visual?.clips.at(-1);
  return (
    uniqueValues(value.tracks.map((track) => track.trackId)) &&
    uniqueValues(value.tracks.map((track) => track.kind)) &&
    visualEnd !== undefined &&
    visualEnd.startMs + visualEnd.durationMs === value.durationMs &&
    value.tracks.every((track) =>
      track.clips.every((clip) => clip.startMs + clip.durationMs <= value.durationMs),
    )
  );
}

const timelineDurationSchema = z
  .number()
  .int()
  .min(MIN_TIMELINE_DURATION_MS)
  .max(MAX_VIDEO_DURATION_MS);
const timelineTracksSchema = z.array(timelineTrackSchema).min(1).max(MAX_TRACKS);

export const editingTimelineDraftSchema = z
  .strictObject({
    durationMs: timelineDurationSchema,
    tracks: timelineTracksSchema,
  })
  .refine(timelineStructureIsValid);
export type EditingTimelineDraft = z.infer<typeof editingTimelineDraftSchema>;

export const editingTimelineSchema = z
  .strictObject({
    timelineId: resourceIdSchema,
    projectId: resourceIdSchema,
    revision: z.number().int().min(1),
    durationMs: timelineDurationSchema,
    tracks: timelineTracksSchema,
    createdAt: timestampSchema,
  })
  .refine(timelineStructureIsValid);
export type EditingTimelineSnapshot = z.infer<typeof editingTimelineSchema>;

export const editingJobStatusSchema = z.enum([
  "queued",
  "running",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
]);
export type EditingJobStatus = z.infer<typeof editingJobStatusSchema>;

export const editingFailureCodeSchema = z.enum([
  "invalid_timeline",
  "material_unavailable",
  "material_unsupported",
  "font_unavailable",
  "render_failed",
  "resource_exhausted",
  "permission_denied",
  "worker_lost",
]);
export type EditingFailureCode = z.infer<typeof editingFailureCodeSchema>;

export const editingJobSchema = z
  .strictObject({
    jobId: resourceIdSchema,
    projectId: resourceIdSchema,
    timelineId: resourceIdSchema,
    timelineRevision: z.number().int().min(1),
    status: editingJobStatusSchema,
    failureCode: editingFailureCodeSchema.nullable(),
    outputArtifactId: resourceIdSchema.nullable(),
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .refine((job) => Date.parse(job.updatedAt) >= Date.parse(job.createdAt))
  .refine((job) => {
    if (job.status === "succeeded") {
      return job.outputArtifactId !== null && job.failureCode === null;
    }
    if (job.status === "failed") {
      return job.outputArtifactId === null && job.failureCode !== null;
    }
    return job.outputArtifactId === null && job.failureCode === null;
  });
export type EditingJobSnapshot = z.infer<typeof editingJobSchema>;
