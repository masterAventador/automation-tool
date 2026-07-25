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

  it("names every part in Chinese only, with no duplicates", () => {
    const titles = MOTION_PARTS_CATALOG.map((part) => part.displayTitle);
    for (const title of titles) {
      expect(title).not.toMatch(/[A-Za-z]/);
    }
    expect(new Set(titles).size).toBe(134);
  });

  it("recommends parts per beat deterministically from the locked catalog", () => {
    const first = recommendMotionPartsForBeat("展示本周销售数据增长", 0);
    const second = recommendMotionPartsForBeat("展示本周销售数据增长", 0);
    expect(first).toEqual(second);
    expect(first.length).toBeGreaterThan(0);
    expect(first.length).toBeLessThanOrEqual(3);
    expect(new Set(first).size).toBe(first.length);
    const ids = new Set(MOTION_PARTS_CATALOG.map((part) => part.id));
    for (const id of first) {
      expect(ids.has(id)).toBe(true);
    }
  });

  it("routes a beat to the category its own words describe", () => {
    const categoryOf = (id: string) =>
      MOTION_PARTS_CATALOG.find((part) => part.id === id)!.category;

    for (const id of recommendMotionPartsForBeat("展示本周销售数据增长", 0)) {
      expect(categoryOf(id)).toBe("数据与地图");
    }
    for (const id of recommendMotionPartsForBeat("介绍这位嘉宾的身份与职位", 1)) {
      expect(categoryOf(id)).toBe("人名与身份条");
    }
  });

  it("understands English and technical words, not only Chinese ones", () => {
    const categoryOf = (id: string) =>
      MOTION_PARTS_CATALOG.find((part) => part.id === id)!.category;
    for (const id of recommendMotionPartsForBeat(
      "本段讲解 RAGFlow 的 API 与 SDK 接入",
      0,
    )) {
      expect(categoryOf(id)).toBe("代码演示");
    }
    const flow = recommendMotionPartsForBeat("用流程图说明三个步骤", 0);
    expect(flow).toContain("flowchart");
    expect(flow).toContain("flowchart-vertical");
  });

  it("picks the part whose own name matches the beat, not the first of a category", () => {
    // Both beats belong to 字幕; the old rule answered every one of them with
    // the first three subtitle parts in id order.
    expect(recommendMotionPartsForBeat("台词逐字跟读字幕", 0)).toContain(
      "caption-pill-karaoke",
    );
    expect(recommendMotionPartsForBeat("字幕用霓虹发光效果", 1)).toContain(
      "caption-neon-glow",
    );

    // Both beats belong to 数据与地图 and must not collapse onto one answer.
    const chart = recommendMotionPartsForBeat("展示本周销售数据增长", 0);
    const map = recommendMotionPartsForBeat("这张地图标出各省份的门店分布", 0);
    expect(chart).toContain("data-chart");
    expect(map).not.toEqual(chart);
    expect(map).not.toContain("data-chart");
  });

  it("gives different beats different parts instead of a fixed top three", () => {
    const topical = ["展示本周销售数据增长", "讲解接口代码改动", "介绍嘉宾身份与职位"].map(
      (text, index) => recommendMotionPartsForBeat(text, index).join(","),
    );
    expect(new Set(topical).size).toBe(3);

    const plain = ["今天天气不错", "随便说说吧", "换个说法试试"].map((text, index) =>
      recommendMotionPartsForBeat(text, index).join(","),
    );
    expect(new Set(plain).size).toBe(3);
  });

  it("can reach far beyond the first three parts of one category", () => {
    const texts = [
      "展示本周销售数据增长",
      "讲解接口代码改动",
      "介绍嘉宾身份与职位",
      "用流程图说明三个步骤",
      "字幕逐句跟读要点",
      "画面切换到下一个镜头",
      "标题开场与口号",
      "社交账号涨粉分享",
      "产品功能界面演示",
      "今天天气不错",
    ];
    const reached = new Set(
      texts.flatMap((text, index) => recommendMotionPartsForBeat(text, index)),
    );
    expect(reached.size).toBeGreaterThanOrEqual(15);
  });
});
