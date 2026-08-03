import type { MotionStyleOption } from "./motion-style-catalog";

export type MotionInformationDensity = "low" | "medium" | "high";

export interface MotionStyleRecommendationRequest {
  readonly brief: string;
  readonly industry: string;
  readonly informationDensity: MotionInformationDensity;
  readonly primaryColor?: string;
}

export interface BrandStyleDraftInput {
  readonly primaryColor: string;
  readonly secondaryColor: string;
  readonly fontFamily: string;
  readonly fontFileName: string;
  readonly logoFileName: string;
}

export interface BrandStyleDraft {
  readonly primaryColor: string | null;
  readonly secondaryColor: string | null;
  readonly fontFamily: string | null;
  readonly fontFileName: string | null;
  readonly logoFileName: string | null;
}

export interface MotionStyleActualContent {
  readonly headline: string;
  readonly body: string;
}

export interface MotionStyleActualPreview extends MotionStyleActualContent {
  readonly paper: string;
  readonly accent: string;
  readonly ink: string;
  readonly fontFamily: string | null;
  readonly fontFileName: string | null;
  readonly logoFileName: string | null;
}

interface RecommendationSignal {
  readonly keywords: readonly string[];
  readonly density: MotionInformationDensity;
}

const RECOMMENDATION_SIGNALS: Readonly<Record<string, RecommendationSignal>> = {
  "biennale-yellow": {
    keywords: ["文化", "展览", "人文", "品牌故事"],
    density: "medium",
  },
  blockframe: {
    keywords: ["年轻", "活动", "社交", "卖点"],
    density: "medium",
  },
  "blue-professional": {
    keywords: ["企业", "商务", "销售", "数据", "增长", "客户", "咨询"],
    density: "high",
  },
  "bold-poster": {
    keywords: ["榜单", "观点", "金句", "标题", "活动"],
    density: "low",
  },
  broadside: {
    keywords: ["宣言", "发布", "趋势", "口号", "观点"],
    density: "low",
  },
  capsule: {
    keywords: ["生活", "新品", "功能", "轻快", "清单"],
    density: "medium",
  },
  cartesian: {
    keywords: ["方法", "步骤", "知识", "专业", "咨询"],
    density: "low",
  },
  "cobalt-grid": {
    keywords: ["数据", "研究", "科技", "复杂", "报告"],
    density: "high",
  },
  coral: {
    keywords: ["快消", "教程", "卖点", "活力", "讲解"],
    density: "medium",
  },
  "creative-mode": {
    keywords: ["设计", "创意", "潮流", "作品", "产品"],
    density: "medium",
  },
  "daisy-days": {
    keywords: ["亲子", "教育", "手作", "温暖", "儿童"],
    density: "low",
  },
  "editorial-forest": {
    keywords: ["自然", "环保", "可持续", "人文", "品质"],
    density: "medium",
  },
};

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/u;
const FONT_FAMILY = /^[\p{L}\p{N} .()'-]{1,80}$/u;
const FONT_FILE = /^[^/\\\0]{1,128}\.(?:woff2?|ttf|otf)$/iu;
const LOGO_FILE = /^[^/\\\0]{1,128}\.(?:png|jpe?g|webp)$/iu;

function requireBoundedText(value: string, label: string, maxLength: number): string {
  const trimmed = value.trim();
  if (trimmed.length === 0 || [...trimmed].length > maxLength) {
    throw new Error(`${label} is out of range`);
  }
  return trimmed;
}

function normalizeOptionalColor(value: string, label: string): string | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  if (!HEX_COLOR.test(trimmed)) {
    throw new Error(`${label} must be #rrggbb`);
  }
  return trimmed.toLowerCase();
}

function colorDistance(left: string, right: string): number {
  const channels = (value: string) => [
    Number.parseInt(value.slice(1, 3), 16),
    Number.parseInt(value.slice(3, 5), 16),
    Number.parseInt(value.slice(5, 7), 16),
  ];
  const a = channels(left);
  const b = channels(right);
  return Math.sqrt(
    (a[0]! - b[0]!) ** 2 + (a[1]! - b[1]!) ** 2 + (a[2]! - b[2]!) ** 2,
  );
}

export function recommendMotionStyles(
  catalog: readonly MotionStyleOption[],
  request: MotionStyleRecommendationRequest,
): readonly MotionStyleOption[] {
  if (catalog.length !== 12 || new Set(catalog.map((style) => style.id)).size !== 12) {
    throw new Error("recommendation requires the locked twelve-style catalog");
  }
  const brief = requireBoundedText(request.brief, "brief", 500);
  const industry = requireBoundedText(request.industry, "industry", 80);
  if (!["low", "medium", "high"].includes(request.informationDensity)) {
    throw new Error("information density is invalid");
  }
  const primary =
    request.primaryColor === undefined || request.primaryColor.trim() === ""
      ? null
      : normalizeOptionalColor(request.primaryColor, "primary color");
  const context = `${brief} ${industry}`.toLowerCase();
  const ranked = catalog.map((style, index) => {
    const signal = RECOMMENDATION_SIGNALS[style.id];
    if (signal === undefined) {
      throw new Error("recommendation signal is missing for a locked style");
    }
    const keywordScore =
      signal.keywords.filter((keyword) => context.includes(keyword.toLowerCase())).length * 10;
    const densityScore = signal.density === request.informationDensity ? 5 : 0;
    const colorScore =
      primary === null ? 0 : Math.max(0, 3 - colorDistance(primary, style.preview.accent) / 100);
    return { style, index, score: keywordScore + densityScore + colorScore };
  });
  ranked.sort((left, right) => right.score - left.score || left.index - right.index);
  return ranked.slice(0, 3).map(({ style }) => style);
}

export function validateBrandStyleDraft(input: BrandStyleDraftInput): BrandStyleDraft {
  const primaryColor = normalizeOptionalColor(input.primaryColor, "primary color");
  const secondaryColor = normalizeOptionalColor(input.secondaryColor, "secondary color");
  const font = input.fontFamily.trim();
  if (font !== "" && !FONT_FAMILY.test(font)) {
    throw new Error("font family is malformed");
  }
  const fontFile = input.fontFileName.trim();
  if ((font === "") !== (fontFile === "")) {
    throw new Error("font family and local font file must be provided together");
  }
  if (fontFile !== "" && !FONT_FILE.test(fontFile)) {
    throw new Error("font file name is malformed");
  }
  const logo = input.logoFileName.trim();
  if (logo !== "" && !LOGO_FILE.test(logo)) {
    throw new Error("logo file name is malformed");
  }
  return {
    primaryColor,
    secondaryColor,
    fontFamily: font === "" ? null : font,
    fontFileName: fontFile === "" ? null : fontFile,
    logoFileName: logo === "" ? null : logo,
  };
}

export function buildMotionStylePreview(
  style: MotionStyleOption,
  content: MotionStyleActualContent,
  brand: BrandStyleDraft,
): MotionStyleActualPreview {
  const headline = requireBoundedText(content.headline, "preview headline", 80);
  const body = requireBoundedText(content.body, "preview body", 240);
  return {
    headline,
    body,
    paper: brand.secondaryColor ?? style.preview.paper,
    accent: brand.primaryColor ?? style.preview.accent,
    ink: style.preview.ink,
    fontFamily: brand.fontFamily,
    fontFileName: brand.fontFileName,
    logoFileName: brand.logoFileName,
  };
}
