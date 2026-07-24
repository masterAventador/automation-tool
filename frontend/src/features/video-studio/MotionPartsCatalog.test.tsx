import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MotionPartsCatalog } from "./MotionPartsCatalog";

const BEATS = [
  { title: "增长看得见", caption: "本周销售数据增长" },
  { title: "续费驱动增长", caption: "客户持续选择新版" },
  { title: "立即体验新版", caption: "现在开始下一步行动" },
] as const;

function renderCatalog(
  selections: readonly (readonly string[])[] = [[], [], []],
  onSelectionsChange = vi.fn(),
) {
  render(
    <MotionPartsCatalog
      beats={BEATS}
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
    expect(within(browser).getByText("Data Chart")).toBeInTheDocument();

    fireEvent.click(within(browser).getByRole("radio", { name: "数据与地图" }));
    const filtered = within(browser).getAllByRole("listitem");
    expect(filtered.length).toBeLessThan(134);
    expect(filtered.length).toBeGreaterThan(0);
    expect(within(browser).getByText("Data Chart")).toBeInTheDocument();
  });

  it("shows performance, device, applicability and provenance for each part", () => {
    renderCatalog();
    const browser = screen.getByRole("region", { name: "动效零件目录" });
    const card = within(browser).getByText("Data Chart").closest("li");
    expect(card).not.toBeNull();
    const scoped = within(card as HTMLElement);
    expect(scoped.getByText(/性能：轻量/)).toBeInTheDocument();
    expect(scoped.getByText(/设备：任意设备/)).toBeInTheDocument();
    expect(scoped.getByText(/适用：数据指标与地理信息/)).toBeInTheDocument();
    expect(scoped.getByText(/来源：文字已本地化/)).toBeInTheDocument();
    expect(scoped.getByText("有官方在线预览")).toBeInTheDocument();
  });

  it("never renders raw part identifiers or upstream words", () => {
    renderCatalog();
    expect(screen.queryByText(/apple-money-count/)).not.toBeInTheDocument();
    expect(screen.getByText("星云科技 Money Count")).toBeInTheDocument();
    expect(screen.queryByText(/整体风格样式包/)).not.toBeInTheDocument();
  });

  it("labels parts as 零件 and never as 整体风格", () => {
    renderCatalog();
    expect(screen.getByText(/动效零件与 12 套整体风格不同/)).toBeInTheDocument();
  });

  it("toggles a part for the active beat and respects the override", () => {
    const onChange = renderCatalog([["data-chart"], [], []]);
    const overrides = screen.getByRole("region", { name: "分镜零件选用" });
    expect(within(overrides).getByText(/第 1 段：已选 1 项/)).toBeInTheDocument();

    const browser = screen.getByRole("region", { name: "动效零件目录" });
    const card = within(browser).getByText("Data Chart").closest("li");
    fireEvent.click(
      within(card as HTMLElement).getByRole("button", { name: /从第 1 段移除/ }),
    );
    expect(onChange).toHaveBeenCalledWith([[], [], []]);
  });

  it("applies deterministic recommendations for a beat on demand", () => {
    const onChange = renderCatalog();
    const overrides = screen.getByRole("region", { name: "分镜零件选用" });
    fireEvent.click(
      within(overrides).getAllByRole("button", { name: "自动推荐" })[0]!,
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]![0] as readonly (readonly string[])[];
    expect(next[0]!.length).toBeGreaterThan(0);
    expect(next[0]!.length).toBeLessThanOrEqual(3);
  });
});
