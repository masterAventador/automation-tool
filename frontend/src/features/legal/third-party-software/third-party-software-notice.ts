import projection from "../../../../../contracts/quality/third-party-notice-ui.v1.json";

/**
 * The disclosure model behind the one page that is allowed to name the upstream
 * projects (`contracts/quality/user-facing-terminology.v1.json` ->
 * `allowedLegalDisclosurePaths`).
 *
 * Versions, commits, licences and counts are never restated here. They come
 * from `contracts/quality/third-party-notice-ui.v1.json`, a projection that
 * `scripts/build_third_party_notice_ui_projection.py` composes from the locked
 * source, asset-rights and motion-rights contracts and that
 * `scripts/check_third_party_notice_ui_projection.py` fails closed on drift.
 * Importing those source contracts directly would ship 87 KB of internal review
 * detail — trademark indicators, sample asset paths, CDN addresses — to every
 * user for the sake of about ten numbers, so the same check refuses any
 * frontend source that reaches behind the projection.
 *
 * Only the Chinese wording a user needs lives in this module, keyed by the
 * projection's own stable identifiers.
 */

export interface UpstreamProjectRecord {
  readonly id: string;
  readonly name: string;
  readonly repository: string;
  readonly sourceUrl: string;
  readonly version: string;
  readonly commit: string;
  readonly license: string;
}

export interface UpstreamProjectPresentation {
  /** The product name a user actually sees for this capability. */
  readonly productFeature: string;
  readonly usage: string;
  readonly boundary: string;
}

export interface UpstreamProjectNotice
  extends UpstreamProjectRecord,
    UpstreamProjectPresentation {}

export interface AssetRightsCategoryRecord {
  readonly id: string;
  readonly requiredFieldCount: number;
}

export interface AssetRightsRecord {
  readonly deniedByDefault: boolean;
  readonly sharedRequiredFieldCount: number;
  readonly registeredEntryCount: number;
  readonly categories: readonly AssetRightsCategoryRecord[];
}

export interface AssetRightsCategoryNotice extends AssetRightsCategoryRecord {
  readonly label: string;
}

export interface AssetRightsNotice {
  readonly deniedByDefault: boolean;
  readonly sharedRequiredFieldCount: number;
  readonly registeredEntryCount: number;
  readonly categories: readonly AssetRightsCategoryNotice[];
}

export interface BorrowedPackageRecord {
  readonly name: string;
  readonly license: string;
  readonly partCount: number;
}

export interface MotionAssetRightsRecord {
  readonly codeLicense: string;
  readonly version: string;
  readonly totalPartCount: number;
  readonly clearedPartCount: number;
  readonly partsNeedingWorkCount: number;
  readonly webFontFamilyCount: number;
  readonly bundledSampleAssetPartCount: number;
  readonly networkDependentPartCount: number;
  readonly dependencies: readonly BorrowedPackageRecord[];
}

export type MotionAssetRightsNotice = MotionAssetRightsRecord;

/** The rights review writes this when it found no licence statement at all. */
const UNVERIFIED_LICENCE = "unverified";
const UNVERIFIED_LICENCE_LABEL = "待确认";

/**
 * Chinese wording is attached by the projection's stable identifier, never by
 * copying a name, version or licence out of it.
 */
export const UPSTREAM_PROJECT_PRESENTATION: Readonly<
  Record<string, UpstreamProjectPresentation>
> = {
  moneyprinterturbo: {
    productFeature: "智能素材成片",
    usage:
      "把一段文字稿转成旁白、字幕，并自动匹配城市、办公、做饭等补充画面，合成为一条可直接发布的竖屏视频。",
    boundary:
      "以只读方式固定在下面这个版本上使用，产品不修改它的代码，也不跟随它的最新分支。",
  },
  hyperframes: {
    productFeature: "品牌动效成片",
    usage:
      "提供整体画面风格与动效零件的网页动画实现，用来按品牌颜色生成标题、产品界面、数据图表和转场动画。",
    boundary:
      "同样以只读方式固定在下面这个版本上使用；它的代码许可证不覆盖字体、音频、示例素材和商标。",
  },
};

/** Chinese names for the rights categories the policy denies by default. */
export const ASSET_CATEGORY_LABELS: Readonly<Record<string, string>> = {
  font: "字体",
  stock_media: "图片与视频素材",
  music_sfx: "音乐与音效",
  codec_binary: "视频编解码程序",
  map_3d: "地图与三维数据",
  generated: "模型生成内容",
};

export function buildUpstreamProjectNotices(
  projects: readonly UpstreamProjectRecord[],
  presentation: Readonly<Record<string, UpstreamProjectPresentation>>,
): readonly UpstreamProjectNotice[] {
  if (projects.length === 0) {
    throw new Error("third-party notice: the projection discloses no upstream project");
  }
  return projects.map((project) => {
    if (project.name.trim() === "" || project.repository.trim() === "") {
      throw new Error(`third-party notice: ${project.id} discloses no project name`);
    }
    if (project.version.trim() === "") {
      throw new Error(`third-party notice: ${project.id} discloses no released version`);
    }
    if (project.license.trim() === "") {
      throw new Error(`third-party notice: ${project.id} discloses no licence`);
    }
    if (project.commit.trim() === "") {
      throw new Error(`third-party notice: ${project.id} discloses no pinned commit`);
    }
    const wording = presentation[project.id];
    if (wording === undefined) {
      throw new Error(`third-party notice: ${project.id} has no Chinese explanation`);
    }
    return { ...project, ...wording };
  });
}

export function buildAssetRightsNotice(
  rights: AssetRightsRecord,
  labels: Readonly<Record<string, string>>,
): AssetRightsNotice {
  if (!rights.deniedByDefault) {
    throw new Error(
      "asset rights notice: the policy no longer defaults to deny for unregistered material",
    );
  }
  if (rights.sharedRequiredFieldCount <= 0) {
    throw new Error("asset rights notice: the policy requires no rights information");
  }
  if (rights.categories.length === 0) {
    throw new Error("asset rights notice: the policy covers no rights category");
  }
  const categories = rights.categories.map((category) => {
    const label = labels[category.id];
    if (label === undefined) {
      throw new Error(`asset rights notice: category ${category.id} has no Chinese label`);
    }
    if (category.requiredFieldCount <= 0) {
      throw new Error(
        `asset rights notice: category ${category.id} requires no rights information`,
      );
    }
    return { ...category, label };
  });
  return {
    deniedByDefault: true,
    sharedRequiredFieldCount: rights.sharedRequiredFieldCount,
    registeredEntryCount: rights.registeredEntryCount,
    categories,
  };
}

export function buildMotionAssetRightsNotice(
  review: MotionAssetRightsRecord,
): MotionAssetRightsNotice {
  if (review.codeLicense.trim() === "" || review.version.trim() === "") {
    throw new Error("third-party notice: the motion rights summary discloses no licence");
  }
  if (review.totalPartCount <= 0) {
    throw new Error("third-party notice: the motion rights summary counted no part");
  }
  if (review.dependencies.length === 0) {
    throw new Error("third-party notice: the motion rights summary lists no dependency");
  }
  const dependencies = review.dependencies.map((entry) => {
    if (entry.name.trim() === "") {
      throw new Error("third-party notice: a borrowed package has no name");
    }
    if (entry.license.trim() === "") {
      throw new Error(`third-party notice: ${entry.name} records no licence`);
    }
    if (entry.partCount <= 0) {
      throw new Error(`third-party notice: ${entry.name} is used by no part`);
    }
    return {
      name: entry.name,
      license:
        entry.license === UNVERIFIED_LICENCE ? UNVERIFIED_LICENCE_LABEL : entry.license,
      partCount: entry.partCount,
    };
  });
  return { ...review, dependencies };
}

export const UPSTREAM_PROJECT_NOTICES: readonly UpstreamProjectNotice[] =
  buildUpstreamProjectNotices(projection.upstreamProjects, UPSTREAM_PROJECT_PRESENTATION);

export const ASSET_RIGHTS_NOTICE: AssetRightsNotice = buildAssetRightsNotice(
  projection.assetRights,
  ASSET_CATEGORY_LABELS,
);

export const MOTION_ASSET_RIGHTS_NOTICE: MotionAssetRightsNotice =
  buildMotionAssetRightsNotice(projection.motionAssetRights);
