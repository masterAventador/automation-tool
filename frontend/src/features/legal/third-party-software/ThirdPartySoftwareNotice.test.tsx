import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ThirdPartySoftwareNotice } from "./ThirdPartySoftwareNotice";
import {
  ASSET_RIGHTS_NOTICE,
  DISTRIBUTED_COMPONENT_NOTICES,
  LICENSE_TEXTS,
  LICENSE_TEXT_LABELS,
  MOTION_ASSET_RIGHTS_NOTICE,
  UPSTREAM_PROJECT_NOTICES,
} from "./third-party-software-notice";

const DISTRIBUTED_REGION = "随安装包分发的第三方组件";
const LICENSE_TEXT_REGION = "许可证全文";

/** Match a literal fact, not a pattern: versions and paths carry regex syntax. */
function literal(value: string): RegExp {
  return new RegExp(value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&"), "u");
}

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

  it("states how many assets the rights register actually cleared for distribution", () => {
    render(<ThirdPartySoftwareNotice />);

    // The register stopped being empty once the release replaced the four
    // proprietary system fonts with open-licensed ones, so the page must say so
    // rather than keep claiming it bundles no third-party asset at all.
    const region = screen.getByRole("region", { name: "字体与素材权利" });
    expect(ASSET_RIGHTS_NOTICE.registeredEntryCount).toBeGreaterThan(0);
    expect(
      within(region).getByText(
        new RegExp(`已登记\\s*${ASSET_RIGHTS_NOTICE.registeredEntryCount}\\s*条`, "u"),
      ),
    ).toBeInTheDocument();
    expect(within(region).getByText(/一律不随安装包分发/u)).toBeInTheDocument();
  });

  it("reproduces the copyright notice the open font licence demands", () => {
    render(<ThirdPartySoftwareNotice />);

    // The SIL OFL text names no copyright holder, so the licence text alone
    // would satisfy only half of its section 1.
    const fonts = DISTRIBUTED_COMPONENT_NOTICES.find(
      (component) => component.license === "OFL-1.1",
    );
    expect(fonts).toBeDefined();
    expect(fonts?.copyright ?? "").not.toBe("");
    const region = screen.getByRole("region", { name: "随安装包分发的第三方组件" });
    expect(
      within(region).getByText(`版权声明：${fonts?.copyright ?? ""}`),
    ).toBeInTheDocument();
    expect(
      within(region).getByText(`安装包内许可证文件：${fonts?.packagedNoticePath ?? ""}`),
    ).toBeInTheDocument();
    // The text itself is covered by the "read every licence in full" case,
    // which already iterates every shipped licence including this one.
    expect(LICENSE_TEXTS["ofl-1.1"]).toContain("SIL OPEN FONT LICENSE Version 1.1");
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

  it("discloses every component the installer actually redistributes", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: DISTRIBUTED_REGION });
    expect(within(region).getAllByRole("listitem")).toHaveLength(
      DISTRIBUTED_COMPONENT_NOTICES.length,
    );
    for (const component of DISTRIBUTED_COMPONENT_NOTICES) {
      expect(within(region).getByText(component.name)).toBeInTheDocument();
      // A version or a licence can legitimately appear more than once — the
      // FFmpeg version is also part of its bundled source archive name.
      expect(
        within(region).getAllByText(literal(component.version)).length,
      ).toBeGreaterThan(0);
      expect(
        within(region).getAllByText(literal(component.license)).length,
      ).toBeGreaterThan(0);
      expect(within(region).getByText(component.role)).toBeInTheDocument();
      expect(within(region).getByText(component.noticeHint)).toBeInTheDocument();
    }
  });

  it("names the GPL media toolchain the notice used to omit outright", () => {
    render(<ThirdPartySoftwareNotice />);

    // The page shipped for months with no FFmpeg entry, no GPL text and no word
    // about where the corresponding source is, while the installer carried all
    // three. That is the defect this assertion exists to keep fixed.
    const region = screen.getByRole("region", { name: DISTRIBUTED_REGION });
    expect(within(region).getByText("FFmpeg")).toBeInTheDocument();
    expect(within(region).getByText("x264")).toBeInTheDocument();
    expect(within(region).getAllByText(/GPL-3\.0-or-later/u).length).toBeGreaterThan(0);
  });

  it("says where the corresponding source of every copyleft component is", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: DISTRIBUTED_REGION });
    const copyleft = DISTRIBUTED_COMPONENT_NOTICES.filter((entry) => entry.copyleft);
    expect(copyleft.length).toBeGreaterThan(0);
    for (const component of copyleft) {
      for (const path of component.packagedSourcePaths) {
        expect(within(region).getByText(literal(path))).toBeInTheDocument();
      }
      expect(
        within(region).getByText(literal(component.upstreamSourceUrl!)),
      ).toBeInTheDocument();
    }
  });

  it("publishes the in-package location of every notice file it points at", () => {
    render(<ThirdPartySoftwareNotice />);

    const paths = [...DISTRIBUTED_COMPONENT_NOTICES, ...UPSTREAM_PROJECT_NOTICES]
      .map((entry) => entry.packagedNoticePath)
      .filter((path): path is string => path !== null);
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      // One licence file may cover several components, so a shared path is
      // published once per component that relies on it.
      expect(screen.getAllByText(literal(path)).length).toBeGreaterThan(0);
    }
  });

  it("reproduces the upstream copyright notice for every locked project", () => {
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: "上游开源项目" });
    for (const notice of UPSTREAM_PROJECT_NOTICES) {
      expect(within(region).getByText(literal(notice.copyright))).toBeInTheDocument();
    }
  });

  it("lets a user read every licence text in full without leaving the App", async () => {
    const user = userEvent.setup();
    render(<ThirdPartySoftwareNotice />);

    const region = screen.getByRole("region", { name: LICENSE_TEXT_REGION });
    for (const [identifier, text] of Object.entries(LICENSE_TEXTS)) {
      // Collapsed on arrival so the page stays readable; the text is already in
      // the bundle, so expanding it cannot fail on a user's machine.
      expect(
        within(region).queryByTestId(`license-text-${identifier}`),
      ).not.toBeInTheDocument();
      const trigger = within(region).getByRole("button", {
        name: literal(LICENSE_TEXT_LABELS[identifier]!),
      });
      await user.click(trigger);
      const shown = await within(region).findByTestId(`license-text-${identifier}`);
      // Rendered verbatim, not summarised: the whole licence is on the page.
      expect(shown.textContent).toBe(text);
    }
  });

  it("admits which redistributed software it has not enumerated yet", () => {
    render(<ThirdPartySoftwareNotice />);

    // Claiming a complete list while the App bundle, the local executor runtime
    // and the packaging bootloader are still unlisted would be a worse notice
    // than an honest one.
    const region = screen.getByRole("region", { name: "尚未逐项公示的部分" });
    expect(within(region).getByText(/本机执行器/u)).toBeInTheDocument();
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
