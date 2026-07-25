import { describe, expect, it } from "vitest";

import projection from "../../../../../contracts/quality/third-party-notice-ui.v1.json";
import {
  ASSET_CATEGORY_LABELS,
  ASSET_RIGHTS_NOTICE,
  MOTION_ASSET_RIGHTS_NOTICE,
  UPSTREAM_PROJECT_NOTICES,
  UPSTREAM_PROJECT_PRESENTATION,
  buildAssetRightsNotice,
  buildMotionAssetRightsNotice,
  buildUpstreamProjectNotices,
} from "./third-party-software-notice";

const PROJECT = {
  id: "moneyprinterturbo",
  name: "MoneyPrinterTurbo",
  repository: "harry0703/MoneyPrinterTurbo",
  sourceUrl: "https://github.com/harry0703/MoneyPrinterTurbo.git",
  version: "v1.3.2",
  commit: "b1588e1fdc6c5e54358f66ca2ff323e1dddf1364",
  license: "MIT",
};

const MOTION = {
  codeLicense: "Apache-2.0",
  version: "v0.7.68",
  totalPartCount: 134,
  clearedPartCount: 7,
  partsNeedingWorkCount: 127,
  webFontFamilyCount: 18,
  bundledSampleAssetPartCount: 28,
  networkDependentPartCount: 125,
  dependencies: [{ name: "gsap", license: "GSAP Standard License", partCount: 125 }],
};

describe("upstream project notices", () => {
  it("discloses every project exactly as the projection records it", () => {
    expect(UPSTREAM_PROJECT_NOTICES).toHaveLength(projection.upstreamProjects.length);

    for (const source of projection.upstreamProjects) {
      const notice = UPSTREAM_PROJECT_NOTICES.find((entry) => entry.id === source.id);
      expect(notice, `no disclosure for ${source.id}`).toBeDefined();
      expect(notice!.name).toBe(source.name);
      expect(notice!.repository).toBe(source.repository);
      expect(notice!.version).toBe(source.version);
      expect(notice!.commit).toBe(source.commit);
      expect(notice!.license).toBe(source.license);
      expect(notice!.sourceUrl).toBe(source.sourceUrl);
    }
  });

  it("names the two upstream projects a legal notice has to name", () => {
    const names = UPSTREAM_PROJECT_NOTICES.map((entry) => entry.name);
    expect(names).toContain("MoneyPrinterTurbo");
    expect(names).toContain("hyperframes");

    const repositories = UPSTREAM_PROJECT_NOTICES.map((entry) => entry.repository);
    expect(repositories).toContain("harry0703/MoneyPrinterTurbo");
    expect(repositories).toContain("heygen-com/hyperframes");

    expect(UPSTREAM_PROJECT_NOTICES.map((entry) => entry.license).sort()).toEqual([
      "Apache-2.0",
      "MIT",
    ]);
  });

  it("explains in Chinese what each project does and which product name covers it", () => {
    for (const notice of UPSTREAM_PROJECT_NOTICES) {
      expect(notice.usage).toMatch(/[一-鿿]/u);
      expect(notice.boundary).toMatch(/[一-鿿]/u);
      expect(notice.productFeature).toMatch(/[一-鿿]/u);
    }
    const features = UPSTREAM_PROJECT_NOTICES.map((entry) => entry.productFeature);
    expect(features).toContain("智能素材成片");
    expect(features).toContain("品牌动效成片");
  });

  it("refuses a projection that lost a version, a licence or a name", () => {
    expect(() =>
      buildUpstreamProjectNotices(
        [{ ...PROJECT, version: "" }],
        UPSTREAM_PROJECT_PRESENTATION,
      ),
    ).toThrow(/version/u);

    expect(() =>
      buildUpstreamProjectNotices(
        [{ ...PROJECT, license: "" }],
        UPSTREAM_PROJECT_PRESENTATION,
      ),
    ).toThrow(/licence/u);

    expect(() =>
      buildUpstreamProjectNotices(
        [{ ...PROJECT, name: "", repository: "" }],
        UPSTREAM_PROJECT_PRESENTATION,
      ),
    ).toThrow(/name/u);
  });

  it("refuses a project the page has no Chinese explanation for", () => {
    expect(() =>
      buildUpstreamProjectNotices(
        [{ ...PROJECT, id: "unknown-upstream" }],
        UPSTREAM_PROJECT_PRESENTATION,
      ),
    ).toThrow(/explanation/u);
  });

  it("refuses an empty disclosure outright", () => {
    expect(() => buildUpstreamProjectNotices([], UPSTREAM_PROJECT_PRESENTATION)).toThrow(
      /no upstream project/u,
    );
  });
});

describe("asset rights notice", () => {
  it("reports the deny-by-default policy exactly as the projection states it", () => {
    expect(ASSET_RIGHTS_NOTICE.deniedByDefault).toBe(true);
    expect(ASSET_RIGHTS_NOTICE.sharedRequiredFieldCount).toBe(
      projection.assetRights.sharedRequiredFieldCount,
    );
    expect(ASSET_RIGHTS_NOTICE.registeredEntryCount).toBe(
      projection.assetRights.registeredEntryCount,
    );
    expect(ASSET_RIGHTS_NOTICE.categories).toHaveLength(
      projection.assetRights.categories.length,
    );
  });

  it("gives every rights category a Chinese label instead of its internal name", () => {
    for (const category of ASSET_RIGHTS_NOTICE.categories) {
      expect(category.label).toMatch(/[一-鿿]/u);
      expect(category.label).not.toBe(category.id);
      expect(category.requiredFieldCount).toBeGreaterThan(0);
    }
    const labels = ASSET_RIGHTS_NOTICE.categories.map((category) => category.label);
    expect(labels).toContain("字体");
  });

  it("refuses a category the page cannot name in Chinese", () => {
    expect(() =>
      buildAssetRightsNotice(
        {
          deniedByDefault: true,
          sharedRequiredFieldCount: 12,
          registeredEntryCount: 0,
          categories: [{ id: "unknown_category", requiredFieldCount: 3 }],
        },
        ASSET_CATEGORY_LABELS,
      ),
    ).toThrow(/Chinese label/u);
  });

  it("refuses a policy that stopped denying unregistered assets", () => {
    expect(() =>
      buildAssetRightsNotice(
        {
          deniedByDefault: false,
          sharedRequiredFieldCount: 12,
          registeredEntryCount: 0,
          categories: [{ id: "font", requiredFieldCount: 6 }],
        },
        ASSET_CATEGORY_LABELS,
      ),
    ).toThrow(/deny/u);
  });
});

describe("motion asset rights notice", () => {
  it("republishes the reviewed summary straight from the projection", () => {
    const source = projection.motionAssetRights;
    expect(MOTION_ASSET_RIGHTS_NOTICE.codeLicense).toBe(source.codeLicense);
    expect(MOTION_ASSET_RIGHTS_NOTICE.version).toBe(source.version);
    expect(MOTION_ASSET_RIGHTS_NOTICE.totalPartCount).toBe(source.totalPartCount);
    expect(MOTION_ASSET_RIGHTS_NOTICE.clearedPartCount).toBe(source.clearedPartCount);
    expect(MOTION_ASSET_RIGHTS_NOTICE.partsNeedingWorkCount).toBe(
      source.partsNeedingWorkCount,
    );
    expect(MOTION_ASSET_RIGHTS_NOTICE.webFontFamilyCount).toBe(source.webFontFamilyCount);
    expect(MOTION_ASSET_RIGHTS_NOTICE.bundledSampleAssetPartCount).toBe(
      source.bundledSampleAssetPartCount,
    );
    expect(MOTION_ASSET_RIGHTS_NOTICE.networkDependentPartCount).toBe(
      source.networkDependentPartCount,
    );
    expect(MOTION_ASSET_RIGHTS_NOTICE.partsNeedingWorkCount).toBeGreaterThan(0);
  });

  it("lists every borrowed package with the licence it is believed to carry", () => {
    expect(MOTION_ASSET_RIGHTS_NOTICE.dependencies).toHaveLength(
      projection.motionAssetRights.dependencies.length,
    );
    for (const dependency of MOTION_ASSET_RIGHTS_NOTICE.dependencies) {
      expect(dependency.name).not.toBe("");
      expect(dependency.license).not.toBe("");
      expect(dependency.partCount).toBeGreaterThan(0);
    }
  });

  it("replaces the review's bare 'unverified' marker with Chinese wording", () => {
    const raw = projection.motionAssetRights.dependencies.find(
      (entry) => entry.license === "unverified",
    );
    expect(raw, "the projection no longer has an unverified package").toBeDefined();

    const shown = MOTION_ASSET_RIGHTS_NOTICE.dependencies.find(
      (entry) => entry.name === raw!.name,
    );
    expect(shown!.license).toBe("待确认");
  });

  it("refuses a summary with no part counted", () => {
    expect(() => buildMotionAssetRightsNotice({ ...MOTION, totalPartCount: 0 })).toThrow(
      /no part/u,
    );
  });

  it("refuses a borrowed package with no licence recorded", () => {
    expect(() =>
      buildMotionAssetRightsNotice({
        ...MOTION,
        dependencies: [{ name: "gsap", license: "", partCount: 125 }],
      }),
    ).toThrow(/licence/u);
  });
});
