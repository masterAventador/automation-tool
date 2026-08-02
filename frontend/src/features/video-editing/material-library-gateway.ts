import { z } from "zod";

import { editingResourceIdSchema } from "./video-editing-dto";

export const MAX_MATERIAL_PAGE_ITEMS = 50;
export const MAX_MATERIAL_DESCRIPTION_CHARACTERS = 2_000;
const MAX_MATERIAL_DURATION_MS = 14_400_000;
const MAX_ANALYSIS_ITEMS = 4_096;
const MAX_TRANSCRIPT_CHARACTERS = 100_000;
const MAX_TAGS = 32;
const MAX_TAG_CHARACTERS = 32;
const DIGEST = /^[0-9a-f]{64}$/u;

function safeText(maximum: number) {
  return z
    .string()
    .min(1)
    .refine((value) => [...value].length <= maximum)
    .refine((value) => value === value.trim())
    .refine((value) =>
      [...value].every(
        (character) =>
          character === "\n" || character === "\t" || !/\p{C}/u.test(character),
      ),
    );
}

const positiveDurationSchema = z.number().int().min(1).max(MAX_MATERIAL_DURATION_MS);
const dimensionSchema = z.number().int().min(1).max(8_192);
const speechSegmentSchema = z.tuple([
  z.number().int().min(0).max(MAX_MATERIAL_DURATION_MS),
  z.number().int().min(1).max(MAX_MATERIAL_DURATION_MS),
]);

export const editingMaterialSchema = z
  .strictObject({
    materialId: editingResourceIdSchema,
    kind: z.enum(["image", "video", "audio"]),
    durationMs: positiveDurationSchema.nullable(),
    width: dimensionSchema.nullable(),
    height: dimensionSchema.nullable(),
    contentDigest: z.string().regex(DIGEST),
    hasAudio: z.boolean(),
    audioLoudnessLufs: z.number().min(-70).max(0).nullable(),
    hasSpeech: z.boolean(),
    speechSegmentsMs: z.array(speechSegmentSchema).max(MAX_ANALYSIS_ITEMS),
    speechTranscript: safeText(MAX_TRANSCRIPT_CHARACTERS).nullable(),
    shotBoundariesMs: z
      .array(z.number().int().min(0).max(MAX_MATERIAL_DURATION_MS))
      .max(MAX_ANALYSIS_ITEMS),
    aiDescription: safeText(MAX_MATERIAL_DESCRIPTION_CHARACTERS).nullable(),
    aiTags: z.array(safeText(MAX_TAG_CHARACTERS)).max(MAX_TAGS),
    descriptionSource: z.enum(["ai", "user"]),
    describedAt: z.iso.datetime().nullable(),
  })
  .superRefine((material, context) => {
    const issue = (message: string) => context.addIssue({ code: "custom", message });
    if (
      (material.kind === "image" &&
        (material.durationMs !== null ||
          material.width === null ||
          material.height === null ||
          material.hasAudio)) ||
      (material.kind === "video" &&
        (material.durationMs === null || material.width === null || material.height === null)) ||
      (material.kind === "audio" &&
        (material.durationMs === null ||
          material.width !== null ||
          material.height !== null ||
          !material.hasAudio))
    ) {
      issue("material shape is invalid");
    }
    if (!material.hasAudio && (material.audioLoudnessLufs !== null || material.hasSpeech)) {
      issue("material audio facts are invalid");
    }
    if (
      (!material.hasSpeech &&
        (material.speechSegmentsMs.length !== 0 || material.speechTranscript !== null)) ||
      (material.hasSpeech &&
        (material.speechSegmentsMs.length === 0 || material.speechTranscript === null))
    ) {
      issue("material speech facts are invalid");
    }
    let previousSpeechEnd = 0;
    for (const [start, end] of material.speechSegmentsMs) {
      if (
        start < previousSpeechEnd ||
        end <= start ||
        (material.durationMs !== null && end > material.durationMs)
      ) {
        issue("material speech segments are invalid");
        break;
      }
      previousSpeechEnd = end;
    }
    if (material.kind === "audio" && material.shotBoundariesMs.length !== 0) {
      issue("audio cannot have shot boundaries");
    }
    let previousBoundary = -1;
    for (const boundary of material.shotBoundariesMs) {
      if (
        boundary <= previousBoundary ||
        material.durationMs === null ||
        boundary >= material.durationMs
      ) {
        issue("material shot boundaries are invalid");
        break;
      }
      previousBoundary = boundary;
    }
    if (new Set(material.aiTags).size !== material.aiTags.length) {
      issue("material tags are invalid");
    }
    if (
      (material.aiDescription === null &&
        (material.aiTags.length !== 0 || material.describedAt !== null)) ||
      (material.descriptionSource === "ai" &&
        material.aiDescription !== null &&
        material.describedAt === null) ||
      (material.descriptionSource === "user" &&
        (material.aiDescription === null ||
          material.aiTags.length !== 0 ||
          material.describedAt !== null))
    ) {
      issue("material description facts are invalid");
    }
  });

export type EditingMaterialSnapshot = z.infer<typeof editingMaterialSchema>;

export const materialCursorSchema = z.string().min(1).max(256).regex(/^[A-Za-z0-9_-]+$/u);
export const editingMaterialPageSchema = z
  .strictObject({
    items: z.array(editingMaterialSchema).max(MAX_MATERIAL_PAGE_ITEMS),
    nextCursor: materialCursorSchema.nullable(),
  })
  .refine((page) => new Set(page.items.map((item) => item.materialId)).size === page.items.length);
export type EditingMaterialPage = z.infer<typeof editingMaterialPageSchema>;

export const localMaterialStatusSchema = z.enum([
  "available",
  "unusable_identifier",
  "not_registered",
  "file_missing",
  "file_unreadable",
  "file_changed",
  "registry_unreadable",
  "registry_unwritable",
  "registry_full",
]);
export type LocalMaterialStatus = z.infer<typeof localMaterialStatusSchema>;

export interface MaterialImportOutcome {
  readonly material: EditingMaterialSnapshot;
  readonly deduplicated: boolean;
}

export const materialImportOutcomeSchema = z.strictObject({
  material: editingMaterialSchema,
  deduplicated: z.boolean(),
});

export type MaterialLibraryErrorCode =
  | "invalid_material"
  | "material_service_unavailable"
  | "outcome_uncertain"
  | "unreadable"
  | "source_not_at_rest"
  | "unsafe_path"
  | "undecodable"
  | "no_usable_stream"
  | "unusable_duration"
  | "too_long"
  | "unusable_frame_size"
  | "frame_too_large"
  | "file_too_large"
  | "silent_audio"
  | "probe_crashed"
  | "probe_failed"
  | "workspace_unusable"
  | "source_changed"
  | "compensation_failed"
  | "file_missing"
  | "file_unreadable"
  | "file_changed"
  | "registry_unreadable"
  | "registry_unwritable"
  | "registry_full"
  | "operation_in_progress"
  | "timed_out"
  | "worker_unavailable";

export class MaterialLibraryGatewayError extends Error {
  constructor(
    readonly code: MaterialLibraryErrorCode,
    readonly retryable: boolean,
  ) {
    super("material library operation unavailable");
    this.name = "MaterialLibraryGatewayError";
  }
}

export interface MaterialLibraryGateway {
  listMaterials(cursor: string | null): Promise<EditingMaterialPage>;
  importMaterial(): Promise<MaterialImportOutcome | null>;
  getMaterialStatus(materialId: string): Promise<LocalMaterialStatus>;
  getMaterialPreviewUrl(materialId: string): Promise<string>;
  updateMaterialDescription(
    materialId: string,
    description: string,
  ): Promise<EditingMaterialSnapshot>;
  deleteMaterial(materialId: string): Promise<void>;
}
