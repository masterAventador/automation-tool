import projection from "../../../../../contracts/quality/third-party-notice-ui.v1.json";
import apacheLicenseText from "./license-texts/apache-2.0.txt?raw";
import gplLicenseText from "./license-texts/gpl-3.0.txt?raw";
import mitLicenseText from "./license-texts/mit.txt?raw";
import oflLicenseText from "./license-texts/ofl-1.1.txt?raw";

/**
 * The disclosure model behind the one page that is allowed to name the upstream
 * projects (`contracts/quality/user-facing-terminology.v1.json` ->
 * `allowedLegalDisclosurePaths`).
 *
 * Versions, commits, licences and counts are never restated here. They come
 * from `contracts/quality/third-party-notice-ui.v1.json`, a projection that
 * `scripts/build_third_party_notice_ui_projection.py` composes from the locked
 * source, media-toolchain, browser and Worker contracts and that
 * `scripts/check_third_party_notice_ui_projection.py` fails closed on drift.
 * Importing those source contracts directly would ship 87 KB of internal review
 * detail — trademark indicators, sample asset paths, CDN addresses — to every
 * user for the sake of about ten numbers, so the same check refuses any
 * frontend source that reaches behind the projection.
 *
 * The licence texts are the one thing that is not a projected fact but the
 * verbatim bytes: MIT and Apache-2.0 both oblige a distributor to hand the
 * licence itself to the recipient, and GPL-3.0 section 4 says the same in
 * stronger words. They are imported raw and statically rather than fetched or
 * code-split, because the whole purpose of this page is that the text is
 * readable offline in the installed App; a lazily loaded chunk introduces a
 * failure mode precisely where failure is not acceptable. About 47 KB of text
 * against an installer that already carries a 230 MB browser is not a bundle
 * problem.
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
  readonly copyright: string;
  readonly licenseTextId: string;
  readonly packagedNoticePath: string | null;
}

export interface UpstreamProjectPresentation {
  /** The product name a user actually sees for this capability. */
  readonly productFeature: string;
  readonly usage: string;
  readonly boundary: string;
}

export interface UpstreamProjectNotice
  extends UpstreamProjectRecord,
    UpstreamProjectPresentation {
  /** The verbatim licence text the App carries for this project. */
  readonly licenseText: string;
}

export interface DistributedComponentRecord {
  readonly id: string;
  readonly name: string;
  readonly version: string;
  readonly license: string;
  readonly copyleft: boolean;
  /**
   * The copyright notice, for the licences whose own text names no holder.
   * Derived from the shipped artifact itself by the projection builder, never
   * retyped, and `null` wherever the licence text already carries it.
   */
  readonly copyright: string | null;
  readonly licenseTextId: string | null;
  readonly packagedNoticePath: string | null;
  readonly noticeChannelId: string | null;
  readonly packagedSourcePaths: readonly string[];
  readonly upstreamSourceUrl: string | null;
}

export interface DistributedComponentPresentation {
  /** What this component does for the user, in product language. */
  readonly role: string;
  /** How a user actually reaches this component's licence and notices. */
  readonly noticeHint: string;
}

export interface DistributedComponentNotice
  extends DistributedComponentRecord,
    DistributedComponentPresentation {
  readonly licenseText: string | null;
}

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

/**
 * The verbatim licence texts the App carries, keyed by the projection's ids.
 *
 * Line endings are normalised so a Windows checkout cannot make the same
 * licence hash differently from the one the gate pinned.
 */
export const LICENSE_TEXTS: Readonly<Record<string, string>> = {
  "mit": mitLicenseText.replace(/\r\n/gu, "\n"),
  "apache-2.0": apacheLicenseText.replace(/\r\n/gu, "\n"),
  "gpl-3.0": gplLicenseText.replace(/\r\n/gu, "\n"),
  "ofl-1.1": oflLicenseText.replace(/\r\n/gu, "\n"),
};

/** The one licence here whose text names no copyright holder of its own. */
const OPEN_FONT_LICENSE = "OFL-1.1";

/** How each shipped licence text is announced to a user. */
export const LICENSE_TEXT_LABELS: Readonly<Record<string, string>> = {
  "mit": "MIT",
  "apache-2.0": "Apache-2.0",
  "gpl-3.0": "GPL-3.0",
  "ofl-1.1": "SIL Open Font License 1.1",
};

/**
 * Chinese wording for every component the installer redistributes, keyed by the
 * projection's stable identifiers.
 *
 * `noticeHint` is the part that matters legally: it is where a user is told how
 * to actually reach the licence, not merely that one exists.
 */
export const DISTRIBUTED_COMPONENT_PRESENTATION: Readonly<
  Record<string, DistributedComponentPresentation>
> = {
  "embedded-browser": {
    role: "随安装包分发的浏览器组件，用来在你看得见的窗口里替你操作网页，并为「品牌动效成片」渲染画面。产品不使用你日常用的浏览器。",
    noticeHint:
      "它的完整第三方声明由该浏览器自身提供：在产品打开的浏览器窗口地址栏输入 chrome://credits 即可查看。",
  },
  ffmpeg: {
    role: "随安装包分发的音视频处理程序，两种视频制作方式都用它来合成、转码和导出成片。",
    noticeHint:
      "许可证全文已收录在本页下方，安装包内同样附带一份；对应的完整源码压缩包也随安装包一起分发，路径见下。",
  },
  x264: {
    role: "随安装包分发的 H.264 视频编码库，被静态编入上面那个音视频处理程序，成片的画面就是它压出来的。",
    noticeHint:
      "它按「GPL-2.0 或更高版本」授权，因此整个可执行文件按 GPL-3.0 分发，适用的许可证全文与上一项相同；它自己的源码压缩包也随安装包一起分发。",
  },
  nodejs: {
    role: "随安装包分发的 JavaScript 运行环境，用来在本机跑「品牌动效成片」的渲染服务。",
    noticeHint:
      "它的完整许可证与其自带第三方声明合并在安装包内的 NODE-LICENSE 文件里，路径见下。",
  },
  "material-video-worker-python": {
    role: "随安装包分发的 Python 运行环境，用来在本机跑「智能素材成片」的配音、字幕与合成服务。",
    noticeHint:
      "安装包内附带一份清单文件，逐条列出随包分发的每个 Python 组件、版本和它声明的许可证，路径见下。",
  },
  onnxruntime: {
    role: "随本机执行器分发的模型推理运行库，用来在本地判断素材声音中是否包含人声。",
    noticeHint: "它的 MIT 许可证正文随运行库放在安装包内，路径见下。",
  },
  "silero-vad-model": {
    role: "随本机执行器分发的人声检测模型，只在本地处理音频采样，不把视频发送给模型服务。",
    noticeHint: "模型的 MIT 许可证正文与模型一起放在安装包内，路径见下。",
  },
  "subtitle-fonts": {
    role: "随安装包分发的开源中文字体，「智能素材成片」用它来把字幕画到画面上；字体本身是完整字符集，不做删减。",
    noticeHint:
      "它按 SIL 开放字体许可证授权，许可证要求随字体附带版权声明与许可证正文：正文已收录在本页下方，安装包内也在字体旁边放了一份，路径见下。",
  },
};

/** Chinese names for the rights categories the policy denies by default. */
export const ASSET_CATEGORY_LABELS: Readonly<Record<string, string>> = {
  font: "字体",
  stock_media: "图片与视频素材",
  music_sfx: "音乐与音效",
  codec_binary: "视频编解码程序",
  ml_model: "本地机器学习模型",
  map_3d: "地图与三维数据",
  generated: "模型生成内容",
};

export function buildUpstreamProjectNotices(
  projects: readonly UpstreamProjectRecord[],
  presentation: Readonly<Record<string, UpstreamProjectPresentation>>,
  licenseTexts: Readonly<Record<string, string>>,
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
    // MIT and Apache-2.0 both ask for exactly two things a licence name does
    // not provide: the upstream copyright notice, and the licence itself.
    if (!project.copyright.trim().startsWith("Copyright")) {
      throw new Error(
        `third-party notice: ${project.id} does not reproduce its copyright notice`,
      );
    }
    const licenseText = licenseTexts[project.licenseTextId];
    if (licenseText === undefined || licenseText.trim() === "") {
      throw new Error(
        `third-party notice: ${project.id} has no licence text shipped with the App`,
      );
    }
    const wording = presentation[project.id];
    if (wording === undefined) {
      throw new Error(`third-party notice: ${project.id} has no Chinese explanation`);
    }
    return { ...project, ...wording, licenseText };
  });
}

/**
 * Turn the redistributed components into what the page publishes about them.
 *
 * The rules mirror `scripts/check_third_party_notice_ui_projection.py`: this is
 * the last place the page can still refuse to render a disclosure that does not
 * satisfy the licence, and it refuses loudly rather than rendering a component
 * whose licence a user has no way to read.
 */
export function buildDistributedComponentNotices(
  components: readonly DistributedComponentRecord[],
  presentation: Readonly<Record<string, DistributedComponentPresentation>>,
  licenseTexts: Readonly<Record<string, string>>,
): readonly DistributedComponentNotice[] {
  if (components.length === 0) {
    throw new Error("third-party notice: the projection discloses no distributed component");
  }
  return components.map((component) => {
    if (component.name.trim() === "" || component.version.trim() === "") {
      throw new Error(`third-party notice: ${component.id} discloses no name or version`);
    }
    if (component.license.trim() === "") {
      throw new Error(`third-party notice: ${component.id} discloses no licence`);
    }
    // The SIL OFL text is a template that names no holder, so section 1 is only
    // half satisfied by the licence text: the copyright notice has to travel
    // with it, which is why the projection reads it out of the font binary.
    if (
      component.license === OPEN_FONT_LICENSE &&
      (component.copyright ?? "").trim() === ""
    ) {
      throw new Error(
        `third-party notice: ${component.id} publishes no copyright notice beside its open font licence`,
      );
    }
    let licenseText: string | null = null;
    if (component.licenseTextId !== null) {
      const text = licenseTexts[component.licenseTextId];
      if (text === undefined) {
        throw new Error(
          `third-party notice: ${component.id} points at a licence text the App does not ship`,
        );
      }
      licenseText = text;
    }
    const reachable =
      licenseText !== null ||
      (component.packagedNoticePath ?? "") !== "" ||
      (component.noticeChannelId ?? "") !== "";
    if (!reachable) {
      throw new Error(
        `third-party notice: ${component.id} publishes no way to read its licence`,
      );
    }
    // GPL-3.0 section 6 is satisfied by the corresponding source travelling in
    // the same package as the binary. Publishing the licence without saying
    // where that source is would leave the strongest obligation unmet.
    if (component.copyleft) {
      if (component.packagedSourcePaths.length === 0) {
        throw new Error(
          `third-party notice: ${component.id} is copyleft but publishes no corresponding source`,
        );
      }
      if ((component.upstreamSourceUrl ?? "") === "") {
        throw new Error(
          `third-party notice: ${component.id} is copyleft but publishes no upstream source address`,
        );
      }
    }
    const wording = presentation[component.id];
    if (wording === undefined) {
      throw new Error(`third-party notice: ${component.id} has no Chinese explanation`);
    }
    return { ...component, ...wording, licenseText };
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
  buildUpstreamProjectNotices(
    projection.upstreamProjects,
    UPSTREAM_PROJECT_PRESENTATION,
    LICENSE_TEXTS,
  );

export const DISTRIBUTED_COMPONENT_NOTICES: readonly DistributedComponentNotice[] =
  buildDistributedComponentNotices(
    projection.distributedComponents,
    DISTRIBUTED_COMPONENT_PRESENTATION,
    LICENSE_TEXTS,
  );

export const ASSET_RIGHTS_NOTICE: AssetRightsNotice = buildAssetRightsNotice(
  projection.assetRights,
  ASSET_CATEGORY_LABELS,
);

export const MOTION_ASSET_RIGHTS_NOTICE: MotionAssetRightsNotice =
  buildMotionAssetRightsNotice(projection.motionAssetRights);
