import { describe, expect, it } from "vitest";

import { MOTION_STYLE_CATALOG } from "./motion-style-catalog";
import {
  buildMotionStylePreview,
  recommendMotionStyles,
  validateBrandStyleDraft,
} from "./motion-style-authoring";

describe("BM-07 motion style recommendation and brand preview", () => {
  it("recommends a stable set of three styles while preserving the full catalog", () => {
    const request = {
      brief: "给企业客户讲清本周销售增长和关键数据",
      industry: "企业服务",
      informationDensity: "high" as const,
      primaryColor: "#2055d6",
    };
    const first = recommendMotionStyles(MOTION_STYLE_CATALOG, request);
    const second = recommendMotionStyles(MOTION_STYLE_CATALOG, request);

    expect(first).toEqual(second);
    expect(first).toHaveLength(3);
    expect(new Set(first.map((style) => style.id)).size).toBe(3);
    expect(first.map((style) => style.id)).toContain("blue-professional");
    expect(MOTION_STYLE_CATALOG).toHaveLength(12);
  });

  it("uses actual copy and validated brand overrides in the preview", () => {
    const style = MOTION_STYLE_CATALOG.find((item) => item.id === "blue-professional")!;
    const brand = validateBrandStyleDraft({
      primaryColor: "#1234ab",
      secondaryColor: "#f2eadb",
      fontFamily: "Acme Sans",
      fontFileName: "AcmeSans-Regular.woff2",
      logoFileName: "acme-logo.png",
    });
    const preview = buildMotionStylePreview(
      style,
      {
        headline: "本周销售增长 38%",
        body: "华东区和续费业务共同推动增长。",
      },
      brand,
    );

    expect(preview.headline).toBe("本周销售增长 38%");
    expect(preview.body).toBe("华东区和续费业务共同推动增长。");
    expect(preview.accent).toBe("#1234ab");
    expect(preview.paper).toBe("#f2eadb");
    expect(preview.fontFamily).toBe("Acme Sans");
    expect(preview.fontFileName).toBe("AcmeSans-Regular.woff2");
    expect(preview.logoFileName).toBe("acme-logo.png");
  });

  it("fails closed on malformed colors, unsafe font names and logo paths", () => {
    expect(() =>
      validateBrandStyleDraft({
        primaryColor: "red",
        secondaryColor: "#ffffff",
        fontFamily: "Inter",
        fontFileName: "Inter.woff2",
        logoFileName: "logo.png",
      }),
    ).toThrow();
    expect(() =>
      validateBrandStyleDraft({
        primaryColor: "#112233",
        secondaryColor: "#ffffff",
        fontFamily: "Acme; background:url(https://evil.example)",
        fontFileName: "Acme.woff2",
        logoFileName: "logo.png",
      }),
    ).toThrow();
    expect(() =>
      validateBrandStyleDraft({
        primaryColor: "#112233",
        secondaryColor: "#ffffff",
        fontFamily: "Acme Sans",
        fontFileName: "AcmeSans-Regular.woff2",
        logoFileName: "../logo.svg",
      }),
    ).toThrow();
    expect(() =>
      validateBrandStyleDraft({
        primaryColor: "#112233",
        secondaryColor: "#ffffff",
        fontFamily: "Acme Sans",
        fontFileName: "",
        logoFileName: "logo.png",
      }),
    ).toThrow(/font family and local font file/u);
    expect(() =>
      validateBrandStyleDraft({
        primaryColor: "#112233",
        secondaryColor: "#ffffff",
        fontFamily: "",
        fontFileName: "AcmeSans-Regular.woff2",
        logoFileName: "logo.png",
      }),
    ).toThrow(/font family and local font file/u);
  });

  it("rejects empty or oversized actual content", () => {
    const style = MOTION_STYLE_CATALOG[0]!;
    const brand = validateBrandStyleDraft({
      primaryColor: "",
      secondaryColor: "",
      fontFamily: "",
      fontFileName: "",
      logoFileName: "",
    });
    expect(() =>
      buildMotionStylePreview(style, { headline: "", body: "正文" }, brand),
    ).toThrow();
    expect(() =>
      buildMotionStylePreview(
        style,
        { headline: "标题", body: "x".repeat(241) },
        brand,
      ),
    ).toThrow();
  });
});
