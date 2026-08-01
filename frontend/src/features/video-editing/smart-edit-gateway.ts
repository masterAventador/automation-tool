import { z } from "zod";

import {
  editingJobSchema,
  editingResourceIdSchema,
  editingTimelineSchema,
} from "./video-editing-dto";

export const smartEditGenerationModeSchema = z.enum(["draft", "render"]);
export type SmartEditGenerationMode = z.infer<typeof smartEditGenerationModeSchema>;

export const smartEditGenerationStatusSchema = z.enum([
  "running",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
]);
export type SmartEditGenerationStatus = z.infer<typeof smartEditGenerationStatusSchema>;

export const smartEditGenerationStageSchema = z.enum([
  "preparing",
  "analyzing",
  "scripting",
  "synthesizing",
  "matching",
  "selecting",
  "publishing",
  "completed",
]);
export type SmartEditGenerationStage = z.infer<typeof smartEditGenerationStageSchema>;

export const smartEditFailureCodeSchema = z.enum([
  "configuration_missing",
  "insufficient_materials",
  "source_too_short",
  "no_relevant_material",
  "material_unavailable",
  "material_snapshot_conflict",
  "timeline_revision_conflict",
  "upstream_rejected",
  "workspace_unusable",
  "commit_failed",
  "render_failed",
  "operation_unavailable",
]);
export type SmartEditFailureCode = z.infer<typeof smartEditFailureCodeSchema>;

const smartEditPromptSchema = z
  .string()
  .min(1)
  .refine((value) => value === value.trim())
  .refine((value) => [...value].length <= 4_000)
  .refine((value) =>
    [...value].every(
      (character) =>
        character === "\n" || character === "\t" || !/\p{C}/u.test(character),
    ),
  );

export const smartEditGenerationRequestSchema = z.strictObject({
  projectId: editingResourceIdSchema,
  prompt: smartEditPromptSchema,
  enableThinking: z.boolean(),
  mode: smartEditGenerationModeSchema,
});
export type SmartEditGenerationRequest = z.infer<typeof smartEditGenerationRequestSchema>;

export const smartEditGenerationSnapshotSchema = z
  .strictObject({
    generationId: editingResourceIdSchema,
    projectId: editingResourceIdSchema,
    mode: smartEditGenerationModeSchema,
    status: smartEditGenerationStatusSchema,
    stage: smartEditGenerationStageSchema.nullable(),
    progressPermille: z.number().int().min(0).max(1_000),
    timeline: editingTimelineSchema.nullable(),
    renderJob: editingJobSchema.nullable(),
    failureCode: smartEditFailureCodeSchema.nullable(),
  })
  .superRefine((snapshot, context) => {
    const active = snapshot.status === "running" || snapshot.status === "cancelling";
    if (
      active &&
      (snapshot.stage === null ||
        snapshot.timeline !== null ||
        snapshot.renderJob !== null ||
        snapshot.failureCode !== null)
    ) {
      context.addIssue({ code: "custom", message: "active generation facts are invalid" });
    }
    if (
      snapshot.status === "succeeded" &&
      (snapshot.stage !== "completed" ||
        snapshot.progressPermille !== 1_000 ||
        snapshot.timeline === null ||
        snapshot.failureCode !== null ||
        (snapshot.mode === "render") !== (snapshot.renderJob !== null))
    ) {
      context.addIssue({ code: "custom", message: "successful generation facts are invalid" });
    }
    if (
      snapshot.status === "failed" &&
      (snapshot.failureCode === null ||
        snapshot.renderJob !== null ||
        (snapshot.failureCode === "render_failed") !== (snapshot.timeline !== null))
    ) {
      context.addIssue({ code: "custom", message: "failed generation facts are invalid" });
    }
    if (
      snapshot.status === "cancelled" &&
      (snapshot.timeline !== null ||
        snapshot.renderJob !== null ||
        snapshot.failureCode !== null)
    ) {
      context.addIssue({ code: "custom", message: "cancelled generation facts are invalid" });
    }
    if (
      snapshot.timeline !== null &&
      snapshot.timeline.projectId !== snapshot.projectId
    ) {
      context.addIssue({ code: "custom", message: "generation project is invalid" });
    }
    if (
      snapshot.renderJob !== null &&
      (snapshot.renderJob.projectId !== snapshot.projectId ||
        snapshot.timeline === null ||
        snapshot.renderJob.timelineId !== snapshot.timeline.timelineId ||
        snapshot.renderJob.timelineRevision !== snapshot.timeline.revision)
    ) {
      context.addIssue({ code: "custom", message: "generation render job is invalid" });
    }
  });
export type SmartEditGenerationSnapshot = z.infer<
  typeof smartEditGenerationSnapshotSchema
>;

export type SmartEditGatewayErrorCode =
  | "invalid_request"
  | "generation_not_found"
  | "generation_not_cancellable"
  | "storage_unavailable"
  | "operation_unavailable"
  | "polling_cancelled"
  | "polling_exhausted";

export class SmartEditGatewayError extends Error {
  constructor(
    readonly code: SmartEditGatewayErrorCode,
    readonly retryable: boolean,
  ) {
    super("smart edit operation unavailable");
    this.name = "SmartEditGatewayError";
  }
}

export interface SmartEditPollOptions {
  readonly signal?: AbortSignal;
}

export interface SmartEditGateway {
  start(request: SmartEditGenerationRequest): Promise<SmartEditGenerationSnapshot>;
  get(generationId: string): Promise<SmartEditGenerationSnapshot>;
  cancel(generationId: string): Promise<SmartEditGenerationSnapshot>;
  waitForTerminal(
    generationId: string,
    options?: SmartEditPollOptions,
  ): Promise<SmartEditGenerationSnapshot>;
}
