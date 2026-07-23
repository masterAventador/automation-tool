import { z } from "zod";

/**
 * Provider-neutral video editing DTOs for the standalone editing workbench.
 *
 * Field names and structural rules mirror the Control Plane editing domain
 * (`EditingProject`, `EditingTimeline`, shared `TimelineTrack` vocabulary and
 * `EditingJob`). Vendor concepts such as provider names, regions, credentials
 * or vendor job identifiers must never appear here.
 */

export const MAX_EDITING_PROJECT_TITLE_CHARACTERS = 200;
export const MAX_EDITING_SOURCE_ARTIFACTS = 256;
export const MAX_VIDEO_DURATION_MS = 600_000;
export const MIN_TIMELINE_DURATION_MS = 100;
export const MAX_TRACKS = 32;
export const MAX_CLIPS_PER_TRACK = 512;
export const MAX_TRANSITION_DURATION_MS = 10_000;
export const MAX_JOB_ARTIFACT_REFERENCES = 64;

const CANONICAL_UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LOCAL_ID = /^[a-z][a-z0-9-]{0,63}$/;

const resourceIdSchema = z.string().regex(CANONICAL_UUID_V4);
const localIdSchema = z.string().regex(LOCAL_ID);
const timestampSchema = z.iso.datetime();

const titleSchema = z
  .string()
  .min(1)
  .max(MAX_EDITING_PROJECT_TITLE_CHARACTERS)
  .refine((value) => value === value.trim() && value.trim().length > 0)
  .refine((value) => !/[\p{Cc}\p{Cf}]/u.test(value));

function uniqueValues(values: readonly string[]): boolean {
  return new Set(values).size === values.length;
}

export const timelineTrackKindSchema = z.enum(["visual", "audio", "caption"]);
export type TimelineTrackKind = z.infer<typeof timelineTrackKindSchema>;

export const transitionKindSchema = z.enum(["cut", "fade", "dissolve", "wipe"]);
export type TransitionKind = z.infer<typeof transitionKindSchema>;

export const timelineTransitionSchema = z.strictObject({
  kind: transitionKindSchema,
  durationMs: z.number().int().min(1).max(MAX_TRANSITION_DURATION_MS),
});
export type TimelineTransitionSnapshot = z.infer<typeof timelineTransitionSchema>;

export const timelineClipSchema = z
  .strictObject({
    clipId: localIdSchema,
    startMs: z.number().int().min(0),
    durationMs: z.number().int().min(1).max(MAX_VIDEO_DURATION_MS),
    sourceArtifactId: resourceIdSchema.nullable(),
    text: z
      .string()
      .min(1)
      .max(2_000)
      .refine((value) => value === value.trim())
      .nullable(),
    transitionIn: timelineTransitionSchema.nullable(),
  })
  .refine((clip) => clip.sourceArtifactId !== null || clip.text !== null);
export type TimelineClipSnapshot = z.infer<typeof timelineClipSchema>;

function clipMatchesTrackKind(kind: TimelineTrackKind, clip: TimelineClipSnapshot): boolean {
  if (kind === "caption") {
    return clip.text !== null && clip.sourceArtifactId === null;
  }
  return clip.sourceArtifactId !== null && clip.text === null;
}

export const timelineTrackSchema = z
  .strictObject({
    trackId: localIdSchema,
    kind: timelineTrackKindSchema,
    clips: z.array(timelineClipSchema).min(1).max(MAX_CLIPS_PER_TRACK),
  })
  .refine((track) => uniqueValues(track.clips.map((clip) => clip.clipId)))
  .refine((track) => track.clips.every((clip) => clipMatchesTrackKind(track.kind, clip)))
  .refine((track) => {
    let previousEnd = 0;
    for (const clip of track.clips) {
      if (clip.startMs < previousEnd) {
        return false;
      }
      previousEnd = clip.startMs + clip.durationMs;
    }
    return true;
  });
export type TimelineTrackSnapshot = z.infer<typeof timelineTrackSchema>;

function timelineStructureIsValid(value: {
  readonly durationMs: number;
  readonly tracks: readonly TimelineTrackSnapshot[];
}): boolean {
  return (
    uniqueValues(value.tracks.map((track) => track.trackId)) &&
    value.tracks.some((track) => track.kind === "visual") &&
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

export const editingProjectSchema = z
  .strictObject({
    projectId: resourceIdSchema,
    title: titleSchema,
    sourceArtifactIds: z.array(resourceIdSchema).max(MAX_EDITING_SOURCE_ARTIFACTS),
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .refine((project) => uniqueValues(project.sourceArtifactIds));
export type EditingProjectSnapshot = z.infer<typeof editingProjectSchema>;

export const editingJobStatusSchema = z.enum([
  "queued",
  "running",
  "paused",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
  "outcome_uncertain",
]);
export type EditingJobStatus = z.infer<typeof editingJobStatusSchema>;

export const editingFailureCodeSchema = z.enum([
  "invalid_input",
  "dependency_unavailable",
  "resource_exhausted",
  "editing_failed",
]);
export type EditingFailureCode = z.infer<typeof editingFailureCodeSchema>;

export const editingJobSchema = z
  .strictObject({
    editingJobId: resourceIdSchema,
    projectId: resourceIdSchema,
    timelineId: resourceIdSchema,
    timelineRevision: z.number().int().min(1),
    status: editingJobStatusSchema,
    inputArtifactIds: z.array(resourceIdSchema).min(1).max(MAX_JOB_ARTIFACT_REFERENCES),
    outputArtifactIds: z.array(resourceIdSchema).max(MAX_JOB_ARTIFACT_REFERENCES),
    failureCode: editingFailureCodeSchema.nullable(),
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .refine((job) => uniqueValues(job.inputArtifactIds))
  .refine((job) => uniqueValues(job.outputArtifactIds))
  .refine((job) =>
    job.inputArtifactIds.every((input) => !job.outputArtifactIds.includes(input)),
  )
  .refine((job) => {
    if (job.status === "succeeded") {
      return job.outputArtifactIds.length > 0 && job.failureCode === null;
    }
    if (job.status === "failed") {
      return job.outputArtifactIds.length === 0 && job.failureCode !== null;
    }
    return job.outputArtifactIds.length === 0 && job.failureCode === null;
  });
export type EditingJobSnapshot = z.infer<typeof editingJobSchema>;
