import { describe, expect, it } from "vitest";

import contract from "../../../../contracts/video/motion-style-presets.v1.json";
import {
  MOTION_STYLE_CATALOG,
  MOTION_STYLE_PRESENTATION,
  buildMotionStyleCatalog,
} from "./motion-style-catalog";

const HEX_COLOR = /^#[0-9a-f]{6}$/u;
const CHINESE = /[一-鿿]/u;

describe("motion style catalog contract consumption", () => {
  it("exposes exactly the twelve locked presets in contract order", () => {
    expect(contract.publicPresetCount).toBe(12);
    expect(MOTION_STYLE_CATALOG).toHaveLength(12);
    expect(MOTION_STYLE_CATALOG.map((style) => style.id)).toEqual(
      contract.presets.map((preset) => preset.id),
    );
    expect(MOTION_STYLE_CATALOG.map((style) => style.displayName)).toEqual(
      contract.presets.map((preset) => preset.displayName),
    );
    expect(MOTION_STYLE_CATALOG.map((style) => style.summary)).toEqual(
      contract.presets.map((preset) => preset.summary),
    );
  });

  it("never exposes internal-only presets or duplicated Chinese names", () => {
    const ids = new Set(MOTION_STYLE_CATALOG.map((style) => style.id));
    for (const internal of contract.internalPresetsNotExposed) {
      expect(ids.has(internal)).toBe(false);
    }
    const names = MOTION_STYLE_CATALOG.map((style) => style.displayName);
    expect(new Set(names).size).toBe(12);
    for (const style of MOTION_STYLE_CATALOG) {
      expect(style.displayName).not.toBe(style.id);
      expect(CHINESE.test(style.displayName)).toBe(true);
    }
  });

  it("gives every style Chinese scenes, Chinese tags and a local color preview", () => {
    for (const style of MOTION_STYLE_CATALOG) {
      expect(style.scenes.length).toBeGreaterThan(0);
      expect(style.tags.length).toBeGreaterThan(0);
      for (const copy of [...style.scenes, ...style.tags, style.summary]) {
        expect(copy.trim()).not.toBe("");
        expect(CHINESE.test(copy)).toBe(true);
        expect(copy).not.toMatch(/https?:|hyperframes|moneyprinter/iu);
      }
      expect(style.preview.paper).toMatch(HEX_COLOR);
      expect(style.preview.accent).toMatch(HEX_COLOR);
      expect(style.preview.ink).toMatch(HEX_COLOR);
    }
  });

  it("fails closed when the catalog drifts from the locked contract", () => {
    const presets = contract.presets;
    const presentation = MOTION_STYLE_PRESENTATION;

    expect(() =>
      buildMotionStyleCatalog(
        { ...contract, presets: presets.slice(0, 11) },
        presentation,
      ),
    ).toThrow();

    expect(() =>
      buildMotionStyleCatalog(
        { ...contract, presets: [...presets.slice(0, 11), presets[0]!] },
        presentation,
      ),
    ).toThrow();

    expect(() =>
      buildMotionStyleCatalog(
        {
          ...contract,
          presets: [
            ...presets.slice(0, 11),
            { id: "code-editorial", displayName: "代码风格", summary: "内部专用风格。" },
          ],
        },
        presentation,
      ),
    ).toThrow();

    const missingPresentation = Object.fromEntries(
      Object.entries(presentation).filter(([key]) => key !== presets[0]!.id),
    );
    expect(() => buildMotionStyleCatalog(contract, missingPresentation)).toThrow();

    expect(() =>
      buildMotionStyleCatalog(contract, {
        ...presentation,
        "code-editorial": Object.values(presentation)[0]!,
      }),
    ).toThrow();
  });
});
