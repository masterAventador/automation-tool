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

  it("keeps saying that the upstream code licence does not cover the material", () => {
    render(<ThirdPartySoftwareNotice />);

    // Apache-2.0 on the motion code is not permission to redistribute the
    // fonts, audio, likenesses and trademarks the parts pull in. Dropping this
    // sentence would make the notice read as broader permission than it is.
    const region = screen.getByRole("region", { name: "字体与素材权利" });
    expect(
      within(region).getByText(
        new RegExp(`${MOTION_ASSET_RIGHTS_NOTICE.codeLicense}[\\s\\S]*只覆盖代码`, "u"),
      ),
    ).toBeInTheDocument();
  });

  it("states honestly that nothing is bundled while the rights register is empty", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: "字体与素材权利" });
    expect(ASSET_RIGHTS_NOTICE.registeredEntryCount).toBe(0);
    expect(within(region).getByText(/尚未随安装包分发/u)).toBeInTheDocument();
    expect(within(region).getByText(/一律不随安装包分发/u)).toBeInTheDocument();
  });

  it("drops the internal rights-review progress the notice never owed anyone", () => {
    render(<ThirdPartySoftwareNotice />);

    // "134 个动效零件，X 个尚未核实，随产品分发前必须先本地化" is work tracking,
    // not a licence disclosure, and every asset it talks about is one this
    // build does not ship. Nothing legally required leaves with it.
    expect(
      screen.queryByRole("heading", { name: "动效零件的权利结论" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "动效零件引用的外部程序包" }),
    ).not.toBeInTheDocument();

    const rendered = document.body.textContent ?? "";
    expect(rendered).not.toContain("尚未核实");
    expect(rendered).not.toContain("初步判定");
    expect(rendered).not.toContain("项通用权利信息");
    expect(rendered).not.toContain("项专门信息");

    for (const dependency of MOTION_ASSET_RIGHTS_NOTICE.dependencies) {
      expect(screen.queryByText(dependency.name)).not.toBeInTheDocument();
    }
    for (const category of ASSET_RIGHTS_NOTICE.categories) {
      expect(screen.queryByText(category.label)).not.toBeInTheDocument();
    }
  });

  it("still carries every fact the three licences oblige it to publish", () => {
    render(<ThirdPartySoftwareNotice />);

    // The trim above is only safe as long as this stays true.
    const region = screen.getByRole("region", { name: "上游开源项目" });
    for (const notice of UPSTREAM_PROJECT_NOTICES) {
      expect(within(region).getByText(notice.name)).toBeInTheDocument();
      expect(within(region).getByText(new RegExp(notice.license, "u"))).toBeInTheDocument();
      expect(within(region).getByText(new RegExp(notice.commit, "u"))).toBeInTheDocument();
      expect(within(region).getByText(new RegExp(notice.sourceUrl, "u"))).toBeInTheDocument();
      expect(within(region).getByText(notice.boundary)).toBeInTheDocument();
    }
  });
});
