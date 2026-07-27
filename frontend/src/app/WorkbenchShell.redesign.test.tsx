import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { WorkbenchShell } from "./WorkbenchShell";

function renderShell() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkbenchShell />
    </QueryClientProvider>,
  );
}

describe("AI-first desktop redesign", () => {
  it("opens on the long-lived AI assistant and exposes the approved navigation", () => {
    renderShell();

    expect(screen.getByRole("heading", { name: "AI 运营助理" })).toBeVisible();
    const navigation = screen.getByRole("navigation", { name: "桌面主导航" });
    for (const label of [
      "AI 助理",
      "热点发现",
      "创作",
      "发布",
      "消息与互动",
      "自动化",
      "账号与平台",
      "设置",
    ]) {
      expect(within(navigation).getByRole("menuitem", { name: label })).toBeVisible();
    }

    expect(screen.getByRole("textbox", { name: "给 AI 助理发消息" })).toBeVisible();
    expect(screen.getByRole("button", { name: "紧急停止" })).toBeVisible();
  });

  it("lets the operator ask the assistant in the same main conversation", async () => {
    const user = userEvent.setup();
    renderShell();

    const composer = screen.getByRole("textbox", { name: "给 AI 助理发消息" });
    await user.type(composer, "查一下新能源热点");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(screen.getAllByText("查一下新能源热点").length).toBeGreaterThan(1);
    expect(screen.getByText(/我会先查询“新能源”的最新热点/u)).toBeVisible();
  });

  it("searches an open keyword and saves it as a long-term hotspot watch", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("menuitem", { name: "热点发现" }));
    const keyword = screen.getByRole("textbox", { name: "热点关键词" });
    await user.clear(keyword);
    await user.type(keyword, "新能源");
    await user.click(screen.getByRole("button", { name: "立即查找" }));

    expect(screen.getByRole("heading", { name: "新能源热点" })).toBeVisible();
    expect(screen.getByText("8 分钟前")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "加入长期监测" }));
    expect(screen.getByText("每 20 分钟监测")).toBeVisible();
  });

  it("shows the source platform on every visible interaction", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("menuitem", { name: "消息与互动" }));
    const list = screen.getByRole("list", { name: "消息与互动列表" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.length).toBeGreaterThan(2);
    for (const row of rows) {
      expect(within(row).getByTestId("message-platform")).toBeVisible();
    }
    expect(screen.getByText("普通评论和私信默认由 AI 自动回复")).toBeVisible();
  });
});
