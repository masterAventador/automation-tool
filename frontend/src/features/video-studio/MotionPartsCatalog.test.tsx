import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MotionPartsCatalog } from "./MotionPartsCatalog";
import type { MotionPartsUsage } from "./motion-parts-catalog";

const BEATS = [
  { title: "增长看得见", caption: "本周销售数据增长" },
  { title: "续费驱动增长", caption: "客户持续选择新版" },
  { title: "立即体验新版", caption: "现在开始下一步行动" },
] as const;

function renderCatalog(
  selections: readonly (readonly string[])[] = [[], [], []],
  onSelectionsChange = vi.fn(),
  usage: MotionPartsUsage = "browse_only",
) {
  render(
    <MotionPartsCatalog
      beats={BEATS}
      usage={usage}
      selections={selections}
      onSelectionsChange={onSelectionsChange}
    />,
  );
  return onSelectionsChange;
}

describe("MotionPartsCatalog", () => {
  it("browses all 134 parts by Chinese category with offline preview cards", () => {
    renderCatalog();
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    expect(within(browser).getAllByRole("listitem")).toHaveLength(134);
    expect(within(browser).getByText("数据图表动画")).toBeInTheDocument();

    fireEvent.click(within(browser).getByRole("radio", { name: "数据与地图" }));
    const filtered = within(browser).getAllByRole("listitem");
    expect(filtered.length).toBeLessThan(134);
    expect(filtered.length).toBeGreaterThan(0);
    expect(within(browser).getByText("数据图表动画")).toBeInTheDocument();
  });

  it("titles the catalog without a hardcoded part count", () => {
    renderCatalog();
    const heading = screen.getByRole("heading", { name: "动效零件目录" });
    expect(heading).toBeInTheDocument();
    expect(heading.textContent).not.toMatch(/\d/);
  });

  it("shows performance, device, applicability and provenance for each part", () => {
    renderCatalog();
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    const card = within(browser).getByText("数据图表动画").closest("li");
    expect(card).not.toBeNull();
    const scoped = within(card as HTMLElement);
    expect(scoped.getByText(/性能：轻量/)).toBeInTheDocument();
    expect(scoped.getByText(/设备：任意设备/)).toBeInTheDocument();
    expect(scoped.getByText(/适用：数据指标与地理信息/)).toBeInTheDocument();
    expect(scoped.getByText(/来源：文字已本地化/)).toBeInTheDocument();
  });

  it("drops the redundant category tag and the unreachable preview tag", () => {
    renderCatalog();
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    const card = within(browser).getByText("数据图表动画").closest("li");
    const scoped = within(card as HTMLElement);
    expect(scoped.queryByText("数据与地图")).toBeNull();
    expect(screen.queryByText("有官方在线预览")).toBeNull();
  });

  it("never renders raw part identifiers or upstream words", () => {
    renderCatalog();
    expect(screen.queryByText(/apple-money-count/)).not.toBeInTheDocument();
    expect(screen.getByText("金额数字滚动")).toBeInTheDocument();
    expect(screen.queryByText(/整体风格样式包/)).not.toBeInTheDocument();
  });

  it("labels parts as 零件 and never as 整体风格", () => {
    renderCatalog();
    expect(screen.getByText(/动效零件与 12 套整体风格不同/)).toBeInTheDocument();
  });

  it("toggles the part specified for the active shot", () => {
    const onChange = renderCatalog(
      [["data-chart"], [], []],
      vi.fn(),
      "applies_to_output",
    );
    const overrides = screen.getByRole("region", { name: "分镜零件选用" });
    expect(
      within(overrides).getByText("第 1 镜头：已指定数据图表动画"),
    ).toBeInTheDocument();

    const browser = screen.getByRole("region", { name: "动效零件目录" });
    const card = within(browser).getByText("数据图表动画").closest("li");
    fireEvent.click(
      within(card as HTMLElement).getByRole("button", {
        name: "取消第 1 镜头的指定",
      }),
    );
    expect(onChange).toHaveBeenCalledWith([[], [], []]);
  });

  it("applies deterministic recommendations for a beat on demand", () => {
    const onChange = renderCatalog([[], [], []], vi.fn(), "applies_to_output");
    const overrides = screen.getByRole("region", { name: "分镜零件选用" });
    fireEvent.click(
      within(overrides).getAllByRole("button", { name: "自动推荐" })[0]!,
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]![0] as readonly (readonly string[])[];
    expect(next[0]!.length).toBeGreaterThan(0);
    expect(next[0]).toHaveLength(1);
  });
});

// The only creation path the App can submit today is the fixed template, whose
// renderer never reads a part id. Until a path that consumes them exists, the
// page has to say so before an operator can tick anything.
describe("MotionPartsCatalog when the creation path ignores part selections", () => {
  it("states it above the catalog, before anything can be ticked", () => {
    renderCatalog();
    const notice = screen.getByRole("alert");
    expect(notice).toHaveTextContent("本次制作方式不会用到零件选择");
    expect(notice).toHaveTextContent("固定模板手工制作");

    const browser = screen.getByRole("region", { name: "动效零件目录" });
    expect(
      notice.compareDocumentPosition(browser) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("disables every part action and labels it instead of offering a beat", () => {
    renderCatalog();
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    expect(
      within(browser).queryAllByRole("button", { name: /加入第 1 段/ }),
    ).toHaveLength(0);
    const actions = within(browser).getAllByRole("button", {
      name: "本次制作不使用",
    });
    expect(actions).toHaveLength(134);
    for (const action of actions) expect(action).toBeDisabled();
  });

  it("never shows a per-beat count the film would honour", () => {
    const onChange = renderCatalog([["data-chart"], [], []]);
    const overrides = screen.getByRole("region", { name: "分镜零件选用" });
    expect(within(overrides).queryAllByText(/已选/)).toHaveLength(0);
    expect(
      within(overrides).getAllByText(/本次制作不使用零件/),
    ).toHaveLength(3);

    const recommend = within(overrides).getAllByRole("button", {
      name: "自动推荐",
    });
    expect(recommend).toHaveLength(3);
    for (const button of recommend) expect(button).toBeDisabled();
    fireEvent.click(recommend[0]!);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("keeps all 134 parts browsable so the capability stays visible", () => {
    renderCatalog();
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    expect(within(browser).getAllByRole("listitem")).toHaveLength(134);

    fireEvent.click(within(browser).getByRole("radio", { name: "数据与地图" }));
    const filtered = within(browser).getAllByRole("listitem");
    expect(filtered.length).toBeLessThan(134);
    expect(filtered.length).toBeGreaterThan(0);
    const card = within(browser).getByText("数据图表动画").closest("li");
    expect(within(card as HTMLElement).getByText(/适用：/)).toBeInTheDocument();
  });
});

describe("MotionPartsCatalog when the creation path consumes part selections", () => {
  it("explains and enables per-shot overrides", () => {
    const onChange = renderCatalog([[], [], []], vi.fn(), "applies_to_output");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "这些指定只用于下一次“一句话自动制作”",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "134 项都可以指定",
    );

    const overrides = screen.getByRole("region", { name: "分镜零件选用" });
    expect(
      within(overrides).getByText("第 1 镜头：由模型自动选择"),
    ).toBeInTheDocument();
    for (const button of within(overrides).getAllByRole("button", {
      name: "自动推荐",
    })) {
      expect(button).toBeEnabled();
    }

    const browser = screen.getByRole("region", { name: "动效零件目录" });
    const card = within(browser).getByText("数据图表动画").closest("li");
    const add = within(card as HTMLElement).getByRole("button", {
      name: "指定给第 1 镜头",
    });
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expect(onChange).toHaveBeenCalledWith([["data-chart"], [], []]);
  });

  it("lets a visual-only catalog entry override the active shot", () => {
    const onChange = renderCatalog([[], [], []], vi.fn(), "applies_to_output");
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    const card = within(browser).getByText("重砸落字字幕").closest("li");
    const add = within(card as HTMLElement).getByRole("button", {
      name: "指定给第 1 镜头",
    });
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expect(onChange).toHaveBeenCalledWith([
      ["caption-kinetic-slam"],
      [],
      [],
    ]);
  });
});
