import { z } from "zod";

const sensitiveAssignment =
  /(?:^|[^a-z0-9_])(?:access[_-]?token|api[_-]?key|authorization|cookie|credential|password|private[_-]?key|refresh[_-]?token|secret|session[_-]?cookie|token)\s*[:=]/i;
const inlineDataUri = /\bdata:[a-z0-9.+-]+\/[a-z0-9.+-]+[^,]*,/i;
const privatePosixPath = /(?:^|[\s"'=])\/(?:users|home|root|tmp|var\/folders)(?:\/|$)/i;
const windowsAbsolutePath = /(?:^|[\s"'=])[a-z]:[\\/]/i;

export const MAX_SEARCH_KEYWORD_CHARACTERS = 80;
export const MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS = 500;
export const MAX_TASK_TARGET_LIMIT = 100;

function containsControlOrBidi(value: string): boolean {
  return Array.from(value).some((character) => {
    const point = character.codePointAt(0);
    return (
      point !== undefined &&
      (point < 0x20 ||
        point === 0x7f ||
        (point >= 0x80 && point <= 0x9f) ||
        (point >= 0x202a && point <= 0x202e) ||
        (point >= 0x2066 && point <= 0x2069))
    );
  });
}

function safeExactText(maximumCharacters: number) {
  return z
    .string()
    .min(1)
    .refine((value) => Array.from(value).length <= maximumCharacters)
    .refine((value) => value.trim() === value)
    .refine((value) => {
      const folded = value.toLowerCase();
      return (
        !containsControlOrBidi(value) &&
        !folded.includes("bearer ") &&
        !folded.includes("file://") &&
        !sensitiveAssignment.test(value) &&
        !inlineDataUri.test(value) &&
        !privatePosixPath.test(value) &&
        !windowsAbsolutePath.test(value)
      );
    });
}

export const douyinSearchKeywordSchema = safeExactText(MAX_SEARCH_KEYWORD_CHARACTERS);

export const douyinActionMessageTemplateSchema = safeExactText(
  MAX_ACTION_MESSAGE_TEMPLATE_CHARACTERS,
).superRefine((value, context) => {
  let unknownVariable = false;
  const literal = value.replace(/\{\{([a-z][a-z0-9_]*)\}\}/g, (_, variable: string) => {
    if (variable !== "target_display_name") {
      unknownVariable = true;
    }
    return "";
  });
  if (
    unknownVariable ||
    literal.trim().length === 0 ||
    literal.includes("{") ||
    literal.includes("}")
  ) {
    context.addIssue({ code: "custom", message: "Invalid action message template" });
  }
});

export const douyinSearchExposureActionSchema = z.enum([
  "browse",
  "comment",
  "direct_message",
]);

export const douyinSearchExposureDefinitionSchema = z
  .object({
    template: z.literal("douyin.search_exposure.v1"),
    searchKeyword: douyinSearchKeywordSchema,
    action: douyinSearchExposureActionSchema,
    messageTemplate: douyinActionMessageTemplateSchema.nullable(),
    targetLimit: z.number().int().min(1).max(MAX_TASK_TARGET_LIMIT),
    minimumIntervalSeconds: z.number().int().min(1).max(3600),
    maximumIntervalSeconds: z.number().int().min(1).max(3600),
    previewRequired: z.literal(true),
    finalConfirmationRequired: z.literal(true),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.minimumIntervalSeconds > value.maximumIntervalSeconds) {
      context.addIssue({ code: "custom", message: "Invalid Task interval" });
    }
    if (
      (value.action === "browse" && value.messageTemplate !== null) ||
      (value.action !== "browse" && value.messageTemplate === null)
    ) {
      context.addIssue({ code: "custom", message: "Invalid Task message relation" });
    }
  });

export type DouyinSearchExposureTaskDefinition = z.infer<
  typeof douyinSearchExposureDefinitionSchema
>;
