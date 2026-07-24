import { describe, expect, it } from "vitest";

import {
  MOTION_PARTS_CATALOG,
  MOTION_PARTS_CATEGORIES,
  groupMotionPartsByCategory,
  recommendMotionPartsForBeat,
} from "./motion-parts-catalog";

describe("motion parts catalog projection", () => {
  it("exposes exactly the locked 134 parts across 11 Chinese categories", () => {
    expect(MOTION_PARTS_CATALOG).toHaveLength(134);
    expect(MOTION_PARTS_CATEGORIES).toHaveLength(11);
    const ids = MOTION_PARTS_CATALOG.map((part) => part.id);
    expect(new Set(ids).size).toBe(134);
    for (const part of MOTION_PARTS_CATALOG) {
      expect(MOTION_PARTS_CATEGORIES).toContain(part.category);
      expect(part.displayTitle.length).toBeGreaterThan(0);
      expect(["完整画面块", "局部组件"]).toContain(part.typeLabel);
      expect(["轻量", "中等", "较高", "高"]).toContain(part.performanceLabel);
      expect(part.deviceRequirementLabel.length).toBeGreaterThan(0);
      expect(part.applicabilityLabel.length).toBeGreaterThan(0);
      expect(part.provenanceLabel.length).toBeGreaterThan(0);
    }
  });

  it("groups every part under its category with no leftovers", () => {
    const grouped = groupMotionPartsByCategory();
    const total = MOTION_PARTS_CATEGORIES.reduce(
      (sum, category) => sum + (grouped.get(category)?.length ?? 0),
      0,
    );
    expect(total).toBe(134);
  });

  it("never exposes trademark indicator words in user-visible text", () => {
    const indicator =
      /(?<![0-9a-z])(apple|ios|iphone|macbook|macos|vscode|vs code|visual studio|sf pro|spotify|tiktok|twitter|youtube|instagram|reddit|heygen|hyperframes|bild)(?![0-9a-z])/i;
    for (const part of MOTION_PARTS_CATALOG) {
      const visible = [
        part.displayTitle,
        part.typeLabel,
        part.category,
        part.performanceLabel,
        part.deviceRequirementLabel,
        part.applicabilityLabel,
        part.provenanceLabel,
      ].join(" ");
      expect(indicator.test(visible)).toBe(false);
    }
  });

  it("recommends parts per beat deterministically from the locked catalog", () => {
    const first = recommendMotionPartsForBeat("展示本周销售数据增长", 0);
    const second = recommendMotionPartsForBeat("展示本周销售数据增长", 0);
    expect(first).toEqual(second);
    expect(first.length).toBeGreaterThan(0);
    expect(first.length).toBeLessThanOrEqual(3);
    const ids = new Set(MOTION_PARTS_CATALOG.map((part) => part.id));
    for (const id of first) {
      expect(ids.has(id)).toBe(true);
    }
  });
});
