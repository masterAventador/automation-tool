import contract from "../../../../contracts/video/motion-style-presets.v1.json";

interface MotionStyleContractPreset {
  readonly id: string;
  readonly displayName: string;
  readonly summary: string;
}

interface MotionStyleContract {
  readonly publicPresetCount: number;
  readonly internalPresetsNotExposed: readonly string[];
  readonly presets: readonly MotionStyleContractPreset[];
}

export interface MotionStylePreview {
  readonly paper: string;
  readonly accent: string;
  readonly ink: string;
}

export interface MotionStylePresentation {
  readonly scenes: readonly string[];
  readonly tags: readonly string[];
  readonly preview: MotionStylePreview;
}

export interface MotionStyleOption extends MotionStylePresentation {
  readonly id: string;
  readonly displayName: string;
  readonly summary: string;
}

/**
 * 展示层补充信息只按稳定内部标识挂接，不复制中文名称或适用说明；
 * 中文名与风格身份唯一来源是 contracts/video/motion-style-presets.v1.json。
 */
export const MOTION_STYLE_PRESENTATION: Record<string, MotionStylePresentation> = {
  "biennale-yellow": {
    scenes: ["文化活动", "策展宣传", "人文故事", "品牌故事"],
    tags: ["亮色调", "信息密度中", "可读性高"],
    preview: { paper: "#f6eed6", accent: "#f7d64a", ink: "#2c3a86" },
  },
  blockframe: {
    scenes: ["年轻品牌", "活动预告", "产品卖点", "社交传播"],
    tags: ["亮色调", "信息密度中", "冲击力强"],
    preview: { paper: "#fdf1e3", accent: "#ff5c8a", ink: "#141414" },
  },
  "blue-professional": {
    scenes: ["企业介绍", "数据周报", "咨询方案", "商务合作"],
    tags: ["亮色调", "信息密度高", "可读性高"],
    preview: { paper: "#f4f1ea", accent: "#1f4fd8", ink: "#1c2a4a" },
  },
  "bold-poster": {
    scenes: ["活动宣发", "榜单盘点", "金句观点", "强标题内容"],
    tags: ["亮色调", "信息密度低", "冲击力强"],
    preview: { paper: "#f2ece0", accent: "#d92b2b", ink: "#191919" },
  },
  broadside: {
    scenes: ["行业观点", "品牌宣言", "趋势评论", "硬朗主题"],
    tags: ["高对比", "信息密度低", "冲击力强"],
    preview: { paper: "#f4e9d6", accent: "#ff6a1f", ink: "#141414" },
  },
  capsule: {
    scenes: ["生活方式", "新品介绍", "轻快品牌", "功能清单"],
    tags: ["亮色调", "信息密度中", "亲和感强"],
    preview: { paper: "#fbf3e4", accent: "#f2a0be", ink: "#34302b" },
  },
  cartesian: {
    scenes: ["方法论讲解", "咨询内容", "步骤说明", "专业知识"],
    tags: ["亮色调", "信息密度低", "可读性高"],
    preview: { paper: "#f8f4ec", accent: "#c9c0ae", ink: "#3a372f" },
  },
  "cobalt-grid": {
    scenes: ["数据报告", "研究结论", "科技内容", "复杂信息"],
    tags: ["亮色调", "信息密度高", "可读性高"],
    preview: { paper: "#efe9dc", accent: "#1747c4", ink: "#22345f" },
  },
  coral: {
    scenes: ["快消推广", "教程讲解", "短促卖点", "活力主题"],
    tags: ["亮色调", "信息密度中", "节奏鲜明"],
    preview: { paper: "#fdf4ea", accent: "#ff6f61", ink: "#211d1a" },
  },
  "creative-mode": {
    scenes: ["设计机构", "创意作品", "潮流产品", "幕后过程"],
    tags: ["亮色调", "信息密度中", "冲击力强"],
    preview: { paper: "#f7f1e3", accent: "#ff4d2e", ink: "#101010" },
  },
  "daisy-days": {
    scenes: ["亲子内容", "教育启蒙", "手作分享", "温暖生活"],
    tags: ["亮色调", "信息密度低", "亲和感强"],
    preview: { paper: "#fdeef2", accent: "#ffd23f", ink: "#3c2a4d" },
  },
  "editorial-forest": {
    scenes: ["自然环保", "可持续生活", "人文叙事", "品质品牌"],
    tags: ["亮色调", "信息密度中", "可读性高"],
    preview: { paper: "#f3efe4", accent: "#2e5d3f", ink: "#24402f" },
  },
};

export function buildMotionStyleCatalog(
  source: MotionStyleContract,
  presentation: Record<string, MotionStylePresentation>,
): readonly MotionStyleOption[] {
  const presets = source.presets;
  if (presets.length !== 12 || source.publicPresetCount !== 12) {
    throw new Error("motion style catalog must expose exactly twelve public presets");
  }
  const internal = new Set(source.internalPresetsNotExposed);
  const identifiers = new Set<string>();
  const names = new Set<string>();
  for (const preset of presets) {
    if (internal.has(preset.id)) {
      throw new Error("motion style catalog must not expose internal-only presets");
    }
    if (identifiers.has(preset.id)) {
      throw new Error("motion style catalog has duplicated preset identifiers");
    }
    if (
      preset.displayName.trim() === "" ||
      preset.displayName === preset.id ||
      names.has(preset.displayName)
    ) {
      throw new Error("motion style catalog Chinese names must be unique and non-empty");
    }
    identifiers.add(preset.id);
    names.add(preset.displayName);
  }
  const presentationIds = Object.keys(presentation);
  if (
    presentationIds.length !== presets.length ||
    presentationIds.some((id) => !identifiers.has(id))
  ) {
    throw new Error("motion style presentation must map exactly the public presets");
  }
  return presets.map((preset) => {
    const extra = presentation[preset.id];
    if (
      extra === undefined ||
      extra.scenes.length === 0 ||
      extra.tags.length === 0 ||
      [...extra.scenes, ...extra.tags].some((copy) => copy.trim() === "")
    ) {
      throw new Error("motion style presentation is incomplete");
    }
    return {
      id: preset.id,
      displayName: preset.displayName,
      summary: preset.summary,
      scenes: extra.scenes,
      tags: extra.tags,
      preview: extra.preview,
    };
  });
}

export const MOTION_STYLE_CATALOG: readonly MotionStyleOption[] = buildMotionStyleCatalog(
  contract,
  MOTION_STYLE_PRESENTATION,
);
