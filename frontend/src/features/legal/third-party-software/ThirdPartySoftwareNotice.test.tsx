import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThirdPartySoftwareNotice } from "./ThirdPartySoftwareNotice";
import {
  ASSET_RIGHTS_NOTICE,
  MOTION_ASSET_RIGHTS_NOTICE,
  UPSTREAM_PROJECT_NOTICES,
} from "./third-party-software-notice";

describe("ThirdPartySoftwareNotice", () => {
  it("renders one disclosure block per locked upstream project", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: "上游开源项目" });
    expect(within(region).getAllByRole("listitem")).toHaveLength(
      UPSTREAM_PROJECT_NOTICES.length,
    );

    for (const notice of UPSTREAM_PROJECT_NOTICES) {
      expect(within(region).getByText(notice.name)).toBeInTheDocument();
      expect(within(region).getByText(new RegExp(notice.version, "u"))).toBeInTheDocument();
      expect(within(region).getByText(new RegExp(notice.license, "u"))).toBeInTheDocument();
      expect(within(region).getByText(notice.usage)).toBeInTheDocument();
      expect(
        within(region).getByText(new RegExp(notice.repository, "u")),
      ).toBeInTheDocument();
    }
  });

  it("says which product feature each upstream project stands behind", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: "上游开源项目" });
    expect(within(region).getByText(/智能素材成片/u)).toBeInTheDocument();
    expect(within(region).getByText(/品牌动效成片/u)).toBeInTheDocument();
  });

  it("explains why these names appear here and nowhere else in the App", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: "为什么这些名称只出现在本页" });
    expect(within(region).getByText(/许可证/u)).toBeInTheDocument();
    expect(within(region).getByText(/只出现在本页/u)).toBeInTheDocument();
  });

  it("declares the font and material rights policy with its real numbers", () => {
    render(<ThirdPartySoftwareNotice />);

    // Exact strings, not bare numbers: "12" is a substring of "125" and "127",
    // which are all on this page, so a loose match would be ambiguous.
    const region = screen.getByRole("region", { name: "字体与素材权利" });
    expect(within(region).getByText(/默认拒绝/u)).toBeInTheDocument();
    expect(within(region).getByText("字体")).toBeInTheDocument();
    expect(
      within(region).getByText(
        `每一条要随安装包分发的素材，都必须先登记齐 ${String(
          ASSET_RIGHTS_NOTICE.sharedRequiredFieldCount,
        )} 项通用权利信息。`,
      ),
    ).toBeInTheDocument();
    expect(
      within(region).getByText(
        `其中 ${String(
          MOTION_ASSET_RIGHTS_NOTICE.webFontFamilyCount,
        )} 个网络字体家族与 ${String(
          MOTION_ASSET_RIGHTS_NOTICE.bundledSampleAssetPartCount,
        )} 个自带示例素材的权利尚未核实。`,
      ),
    ).toBeInTheDocument();
    expect(
      within(region).getByText(
        `已核查 ${String(MOTION_ASSET_RIGHTS_NOTICE.totalPartCount)} 个动效零件：${String(
          MOTION_ASSET_RIGHTS_NOTICE.clearedPartCount,
        )} 个可直接使用，${String(
          MOTION_ASSET_RIGHTS_NOTICE.partsNeedingWorkCount,
        )} 个必须先本地化或更换素材才能随产品分发。`,
      ),
    ).toBeInTheDocument();
  });

  it("lists every borrowed package with its licence", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: "字体与素材权利" });
    for (const dependency of MOTION_ASSET_RIGHTS_NOTICE.dependencies) {
      expect(within(region).getByText(dependency.name)).toBeInTheDocument();
    }
  });

  it("states honestly that nothing is bundled while the rights register is empty", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: "字体与素材权利" });
    expect(ASSET_RIGHTS_NOTICE.registeredEntryCount).toBe(0);
    expect(within(region).getByText(/尚未随安装包分发/u)).toBeInTheDocument();
  });
});
