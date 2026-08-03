import contract from "../../../../contracts/video/motion-catalog-ui.v1.json";
import type { MotionVideoDraftRequest } from "./material-video-studio-gateway";

export interface MotionPartOption {
  readonly id: string;
  readonly displayTitle: string;
  readonly typeLabel: string;
  readonly category: string;
  readonly performanceLabel: string;
  readonly deviceRequirementLabel: string;
  readonly applicabilityLabel: string;
  readonly provenanceLabel: string;
}

const ASCII_LETTER = /[A-Za-z]/;

interface MotionPartsContract {
  readonly counts: { readonly total: number };
  readonly categories: readonly string[];
  readonly items: readonly MotionPartOption[];
}

function loadCatalog(): {
  readonly categories: readonly string[];
  readonly parts: readonly MotionPartOption[];
  readonly selectablePartIds: ReadonlySet<string>;
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
  const names = new Set<string>();
  for (const item of payload.items) {
    if (!payload.categories.includes(item.category)) {
      throw new Error("motion parts catalog item has an unknown category");
    }
    if (item.displayTitle.trim() === "") {
      throw new Error("motion parts catalog item is missing a display title");
    }
    if (ASCII_LETTER.test(item.displayTitle)) {
      throw new Error("motion parts catalog item name is not localized");
    }
    if (names.has(item.displayTitle)) {
      throw new Error("motion parts catalog has duplicated part names");
    }
    names.add(item.displayTitle);
  }
  const selectablePartIds = identifiers;
  return {
    categories: payload.categories,
    parts: payload.items,
    selectablePartIds,
  };
}

const loaded = loadCatalog();

export const MOTION_PARTS_CATEGORIES: readonly string[] = loaded.categories;
export const MOTION_PARTS_CATALOG: readonly MotionPartOption[] = loaded.parts;
export const MOTION_SELECTABLE_PART_IDS: ReadonlySet<string> =
  loaded.selectablePartIds;

/**
 * Whether the chosen creation path turns per-beat part selections into pixels.
 *
 * `browse_only` is a fact about the fixed-template path, not a feature flag:
 * its render request carries no part id. The one-sentence path consumes a
 * per-shot override. All 134 locked release items can become visual segments;
 * the separate slot contract only says which of them can also receive beat copy.
 */
export type MotionPartsUsage = "applies_to_output" | "browse_only";

type MotionCreationMode = MotionVideoDraftRequest["creationMode"];

// Keyed by creation mode on purpose: widening the request union stops this
// record from compiling until someone states whether the new mode reads the
// selections. Wiring the one-sentence automatic path is therefore the only
// change needed to give the catalog its interactions back.
const USAGE_BY_CREATION_MODE: Readonly<
  Record<MotionCreationMode, MotionPartsUsage>
> = {
  // The fixed template composes from the overall style, the two colours and
  // the beat text alone; see docs/development/FIX-motion-parts-selection-wiring.md.
  manual_template_v1: "browse_only",
};

export function motionPartsUsage(mode: MotionCreationMode): MotionPartsUsage {
  return USAGE_BY_CREATION_MODE[mode];
}

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

// Deterministic per-beat scoring for the App-side default selection; the
// model-side auto-selection lives in the authoring agent and a user can
// always override whatever this produces.
//
// The rule this replaced routed a beat to one category and then took the first
// three parts of that category in id order, so every beat of a topic got the
// identical answer and 120-odd parts were unreachable without hand picking.
// Scoring instead reads the beat against each part's own words, and the tie
// break is a stable hash of the beat so equally good parts still rotate.
interface TopicRuleSource {
  readonly category: string;
  /** Chinese keywords; every distinct hit adds to the category score. */
  readonly chinese: RegExp;
  /** English and technical words, matched whole; letters and digits only. */
  readonly terms: readonly string[];
}

interface TopicRule extends TopicRuleSource {
  readonly termPatterns: readonly (readonly [string, RegExp])[];
}

const TOPIC_RULE_SOURCES: readonly TopicRuleSource[] = [
  {
    category: "数据与地图",
    chinese: /数据|指标|增长|销售|营收|成交|图表|统计|报表|占比|转化|排名|地图|地区|分布/gu,
    terms: ["chart", "graph", "metric", "metrics", "kpi", "roi", "analytics", "growth", "revenue", "funnel", "map", "region"],
  },
  {
    category: "代码演示",
    chinese: /代码|源码|接口|接入|技术|开发|终端|命令|部署|架构|算法|调试|报错|数据库|脚本|模型/gu,
    terms: ["code", "api", "sdk", "cli", "shell", "terminal", "json", "yaml", "sql", "git", "docker", "python", "typescript", "debug", "bug", "deploy", "server", "database", "ai", "llm", "rag", "ragflow", "agent"],
  },
  {
    category: "字幕",
    chinese: /字幕|台词|旁白|口播|要点|逐字|逐句|念白/gu,
    terms: ["caption", "captions", "subtitle", "subtitles", "karaoke"],
  },
  {
    category: "人名与身份条",
    chinese: /人物|介绍|嘉宾|团队|身份|职位|头衔|专家|讲师|受访/gu,
    terms: ["name", "title", "guest", "speaker", "host"],
  },
  {
    category: "产品与案例展示",
    chinese: /产品|功能|案例|演示|界面|上线|新版|方案|客户|应用/gu,
    terms: ["app", "product", "feature", "demo", "showcase", "case", "release"],
  },
  {
    category: "社交平台展示",
    chinese: /社交|账号|粉丝|评论|点赞|转发|分享|私信|主页|涨粉/gu,
    terms: ["follow", "follower", "followers", "post", "share", "social", "feed"],
  },
  {
    category: "流程图",
    chinese: /流程|步骤|环节|结构|阶段|链路|先后/gu,
    terms: ["flow", "step", "steps", "process", "pipeline"],
  },
  {
    category: "转场",
    chinese: /切换|转场|过渡|镜头|衔接|节奏|下一段|下一个/gu,
    terms: ["transition", "transitions", "cut", "wipe", "zoom", "pan"],
  },
  {
    category: "文字效果",
    chinese: /标题|开场|结尾|收尾|口号|标语|行动|文字|字体/gu,
    terms: ["text", "headline", "slogan", "outro", "intro"],
  },
  {
    category: "画面内复杂效果",
    chinese: /特效|质感|氛围|光影|玻璃|粒子|三维|立体|炫酷/gu,
    terms: ["glass", "portal", "shatter", "liquid", "magnetic"],
  },
  {
    category: "其他",
    chinese: /水印|颗粒|暗角|噪点|滚动条|收尾标识|品牌标识/gu,
    terms: ["grain", "vignette", "ticker", "logo"],
  },
];

const RECOMMENDATION_LIMIT = 3;
const CATEGORY_KEYWORD_WEIGHT = 6;
const ID_TOKEN_WEIGHT = 4;
const NAME_OVERLAP_WEIGHT = 3;
const APPLICABILITY_OVERLAP_WEIGHT = 2;
// With no usable signal at all, prefer the parts that fit any beat rather than
// dropping an operator into a terminal colour scheme.
const NEUTRAL_CATEGORY_SCORES: readonly (readonly [string, number])[] = [
  ["文字效果", 2],
  ["转场", 2],
  ["画面内复杂效果", 1],
];

const CJK_CHARACTER = /[一-鿿]/u;
const ASCII_TOKEN = /[a-z0-9]+/g;

function chineseBigrams(text: string): ReadonlySet<string> {
  const grams = new Set<string>();
  for (let index = 0; index + 1 < text.length; index += 1) {
    const first = text[index]!;
    const second = text[index + 1]!;
    if (CJK_CHARACTER.test(first) && CJK_CHARACTER.test(second)) {
      grams.add(first + second);
    }
  }
  return grams;
}

function sharedBigramCount(
  beatGrams: ReadonlySet<string>,
  text: string,
): number {
  let shared = 0;
  for (const gram of chineseBigrams(text)) {
    if (beatGrams.has(gram)) shared += 1;
  }
  return shared;
}

const PLAIN_TERM = /^[a-z0-9]+$/;

function wholeWordPattern(term: string): RegExp {
  if (!PLAIN_TERM.test(term)) {
    throw new Error(`topic rule term must be plain letters or digits: ${term}`);
  }
  return new RegExp(`(?<![0-9a-z])${term}(?![0-9a-z])`, "iu");
}

const TOPIC_RULES: readonly TopicRule[] = TOPIC_RULE_SOURCES.map((rule) => ({
  ...rule,
  termPatterns: rule.terms.map(
    (term) => [term, wholeWordPattern(term)] as const,
  ),
}));

/** FNV-1a, so equally scored parts rotate per beat yet never per call. */
function stableHash(value: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

function categoryScores(beatText: string): ReadonlyMap<string, number> {
  const scores = new Map<string, number>(NEUTRAL_CATEGORY_SCORES);
  for (const rule of TOPIC_RULES) {
    const hits = new Set(beatText.match(rule.chinese) ?? []);
    for (const [term, pattern] of rule.termPatterns) {
      if (pattern.test(beatText)) hits.add(term);
    }
    if (hits.size === 0) continue;
    scores.set(
      rule.category,
      (scores.get(rule.category) ?? 0) + hits.size * CATEGORY_KEYWORD_WEIGHT,
    );
  }
  return scores;
}

export function recommendMotionPartsForBeat(
  beatText: string,
  beatIndex: number,
): readonly string[] {
  const scores = categoryScores(beatText);
  const beatGrams = chineseBigrams(beatText);
  const beatTokens = new Set(beatText.toLowerCase().match(ASCII_TOKEN) ?? []);

  const ranked = MOTION_PARTS_CATALOG.filter((part) =>
    MOTION_SELECTABLE_PART_IDS.has(part.id),
  ).map((part) => {
    let score = scores.get(part.category) ?? 0;
    for (const token of part.id.split("-")) {
      if (beatTokens.has(token)) score += ID_TOKEN_WEIGHT;
    }
    score += sharedBigramCount(beatGrams, part.displayTitle) * NAME_OVERLAP_WEIGHT;
    score +=
      sharedBigramCount(beatGrams, part.applicabilityLabel) *
      APPLICABILITY_OVERLAP_WEIGHT;
    return {
      id: part.id,
      score,
      tieBreak: stableHash(`${beatIndex} ${beatText} ${part.id}`),
    };
  });

  ranked.sort((left, right) => {
    if (left.score !== right.score) return right.score - left.score;
    if (left.tieBreak !== right.tieBreak) return left.tieBreak - right.tieBreak;
    return left.id.localeCompare(right.id);
  });

  return ranked.slice(0, RECOMMENDATION_LIMIT).map((entry) => entry.id);
}
