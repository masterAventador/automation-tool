import catalogDocument from "../../../../contracts/video/caption-font-catalog.v1.json";
import { z } from "zod";

const FONT_KEY = /^[a-z][a-z0-9-]{0,63}$/u;

const catalogSchema = z
  .object({
    schemaVersion: z.literal(1),
    defaultKey: z.string().regex(FONT_KEY),
    options: z
      .array(
        z
          .object({
            key: z.string().regex(FONT_KEY),
            label: z.string().trim().min(1),
            description: z.string().trim().min(1),
            selectable: z.boolean(),
          })
          .strict(),
      )
      .min(1),
  })
  .strict();

const parsed = catalogSchema.parse(catalogDocument);
const keys = new Set(parsed.options.map((option) => option.key));
if (keys.size !== parsed.options.length || !keys.has(parsed.defaultKey)) {
  throw new Error("caption font catalog has duplicate options or an unknown default");
}
const defaultOption = parsed.options.find((option) => option.key === parsed.defaultKey);
if (defaultOption?.selectable !== true) {
  throw new Error("caption font catalog default must be selectable");
}

export const DEFAULT_CAPTION_FONT_KEY = parsed.defaultKey;
export const SELECTABLE_CAPTION_FONTS = Object.freeze(
  parsed.options.filter((option) => option.selectable),
);

export function captionFontLabel(fontKey: string): string {
  return parsed.options.find((option) => option.key === fontKey)?.label ?? fontKey;
}

export function captionFontDescription(fontKey: string): string {
  return parsed.options.find((option) => option.key === fontKey)?.description ?? "";
}
