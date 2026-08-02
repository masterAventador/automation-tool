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

const SMART_EDIT_FAILURE_TEXT: Readonly<Record<SmartEditFailureCode, string>> = {
  configuration_missing:
    "智能剪辑尚未配置完成，请先到设置中完成服务配置后重试。",
  insufficient_materials: "可用素材不足，请先导入更多素材后重试。",
  source_too_short: "现有素材时长太短，请导入更长的素材后重试。",
  no_relevant_material: "没有找到与描述相符的素材，请调整描述或导入相关素材后重试。",
  material_unavailable: "部分素材当前不可用，请在素材库恢复或重新导入后重试。",
  material_snapshot_conflict: "生成期间素材发生了变化，请确认素材库内容后重新生成。",
  timeline_revision_conflict: "时间轴已被更新，请刷新当前时间轴后重新生成。",
  upstream_rejected: "暂时无法完成内容生成，请稍后重试或缩短描述。",
  workspace_unusable: "本机暂存空间不可用，请释放磁盘空间并确认目录可写后重试。",
  commit_failed: "草稿未能安全保存，请刷新时间轴确认当前内容后重新生成。",
  render_failed: "草稿已生成但成片失败，请检查时间轴后重新提交剪辑任务。",
  operation_unavailable: "智能剪辑当前不可用，请确认本机服务正在运行后重试。",
};

export function smartEditFailureText(code: SmartEditFailureCode): string {
  return SMART_EDIT_FAILURE_TEXT[code];
}

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
  readonly onSnapshot?: (snapshot: SmartEditGenerationSnapshot) => void;
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
