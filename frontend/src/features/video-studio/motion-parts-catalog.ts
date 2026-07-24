import contract from "../../../../contracts/video/motion-catalog-ui.v1.json";

export interface MotionPartOption {
  readonly id: string;
  readonly displayTitle: string;
  readonly typeLabel: string;
  readonly category: string;
  readonly officialPreview: boolean;
  readonly performanceLabel: string;
  readonly deviceRequirementLabel: string;
  readonly applicabilityLabel: string;
  readonly provenanceLabel: string;
}

interface MotionPartsContract {
  readonly counts: { readonly total: number };
  readonly categories: readonly string[];
  readonly items: readonly MotionPartOption[];
}

function loadCatalog(): {
  readonly categories: readonly string[];
  readonly parts: readonly MotionPartOption[];
} {
  const payload = contract as MotionPartsContract;
  if (payload.counts.total !== 134 || payload.items.length !== 134) {
    throw new Error("motion parts catalog must expose exactly 134 locked parts");
  }
  if (payload.categories.length !== 11) {
    throw new Error("motion parts catalog must expose the 11 Chinese categories");
  }
  const identifiers = new Set(payload.items.map((item) => item.id));
  if (identifiers.size !== 134) {
    throw new Error("motion parts catalog has duplicated part identifiers");
  }
  for (const item of payload.items) {
    if (!payload.categories.includes(item.category)) {
      throw new Error("motion parts catalog item has an unknown category");
    }
    if (item.displayTitle.trim() === "") {
      throw new Error("motion parts catalog item is missing a display title");
    }
  }
  return { categories: payload.categories, parts: payload.items };
}

const loaded = loadCatalog();

export const MOTION_PARTS_CATEGORIES: readonly string[] = loaded.categories;
export const MOTION_PARTS_CATALOG: readonly MotionPartOption[] = loaded.parts;

export function groupMotionPartsByCategory(): ReadonlyMap<
  string,
  readonly MotionPartOption[]
> {
  const grouped = new Map<string, MotionPartOption[]>();
  for (const category of MOTION_PARTS_CATEGORIES) {
    grouped.set(category, []);
  }
  for (const part of MOTION_PARTS_CATALOG) {
    grouped.get(part.category)!.push(part);
  }
  return grouped;
}

// Deterministic keyword → Chinese category routing for per-beat auto
// recommendation; the model-side auto-selection lives in the authoring agent
// and this closed rule only produces the App-side default a user can override.
const CATEGORY_KEYWORDS: readonly (readonly [RegExp, string])[] = [
  [/数据|增长|指标|销售|图表|统计|地图/u, "数据与地图"],
  [/代码|接口|技术|开发|终端/u, "代码演示"],
  [/字幕|台词|要点/u, "字幕"],
  [/人物|介绍|团队|嘉宾|身份/u, "人名与身份条"],
  [/产品|功能|案例|演示|界面/u, "产品与案例展示"],
  [/社交|账号|粉丝|评论|分享/u, "社交平台展示"],
  [/流程|步骤|结构|环节/u, "流程图"],
  [/切换|转场|节奏/u, "转场"],
  [/标题|开场|结尾|口号|行动/u, "文字效果"],
];
const FALLBACK_CATEGORIES: readonly string[] = ["文字效果", "画面内复杂效果", "其他"];

export function recommendMotionPartsForBeat(
  beatText: string,
  beatIndex: number,
): readonly string[] {
  let category: string | null = null;
  for (const [pattern, candidate] of CATEGORY_KEYWORDS) {
    if (pattern.test(beatText)) {
      category = candidate;
      break;
    }
  }
  if (category === null) {
    category = FALLBACK_CATEGORIES[
      ((beatIndex % FALLBACK_CATEGORIES.length) + FALLBACK_CATEGORIES.length)
        % FALLBACK_CATEGORIES.length
    ]!;
  }
  const candidates = MOTION_PARTS_CATALOG.filter(
    (part) => part.category === category,
  );
  return candidates.slice(0, 3).map((part) => part.id);
}
