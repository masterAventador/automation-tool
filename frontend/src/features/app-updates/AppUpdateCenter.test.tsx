import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfigProvider } from "antd";

import type { AppUpdateGateway, AppUpdateState } from "./contracts";
import { AppUpdateCenter } from "./AppUpdateCenter";

const release = {
  version: "1.2.3",
  channel: "stable",
  policy: "optional",
  notes: "修复稳定性并改进更新体验。",
  publishedAt: "2026-07-22T00:00:00Z",
  artifact: {
    target: "darwin",
    arch: "aarch64",
    sha256: "a".repeat(64),
    sizeBytes: 2048,
  },
} as const;

function gateway(initial: AppUpdateState): AppUpdateGateway {
  return {
    getState: vi.fn().mockResolvedValue(initial),
    checkNow: vi.fn().mockResolvedValue({ state: "up_to_date", trigger: "manual" }),
    decide: vi.fn().mockImplementation(async (decision) =>
      decision === "defer"
        ? { state: "ready", release, action: "deferred" }
        : { state: "installation_launched", release },
    ),
  };
}

function renderCenter(source: AppUpdateGateway, showSettings: boolean) {
  return render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AppUpdateCenter gateway={source} showSettings={showSettings} />
    </ConfigProvider>,
  );
}

async function visibleRole(role: "button" | "heading", name: string): Promise<HTMLElement> {
  let visible: HTMLElement | undefined;
  await waitFor(() => {
    visible = screen.getAllByRole(role, { name }).at(-1);
    expect(visible).toBeVisible();
  });
  if (visible === undefined) throw new Error("visible update control is missing");
  return visible;
}

describe("generic App update UI", () => {
  it("prompts an optional release and drives install, defer and skip through the gateway", async () => {
    const source = gateway({ state: "ready", release, action: "prompt" });
    const user = userEvent.setup();
    renderCenter(source, true);

    await visibleRole("heading", "发现新版本 1.2.3");
    expect(screen.getAllByText("修复稳定性并改进更新体验。").at(-1)).toBeVisible();
    await visibleRole("button", "立即安装");
    const defer = await visibleRole("button", "稍后提醒");
    await visibleRole("button", "跳过此版本");

    await user.click(defer);
    await waitFor(() => expect(source.decide).toHaveBeenCalledWith("defer"));
    expect(await screen.findByText("已暂缓，将在下次检查时重新提示")).toBeVisible();
  });

  it("keeps a forced release non-dismissible without optional decisions", async () => {
    const source = gateway({
      state: "ready",
      release: { ...release, policy: "forced" },
      action: "forced",
    });
    renderCenter(source, false);

    await visibleRole("heading", "必须更新到 1.2.3");
    expect(
      screen.getAllByText("请重新启动 App，更新将在启动时自动安装。").at(-1),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "稍后提醒" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "跳过此版本" })).not.toBeInTheDocument();
  });

  it("exposes manual checking in settings", async () => {
    const source = gateway({ state: "idle" });
    const user = userEvent.setup();
    renderCenter(source, true);

    expect(await screen.findByRole("heading", { name: "App 更新" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "检查更新" }));
    expect(await screen.findByText("当前已是最新版本")).toBeVisible();
    expect(source.checkNow).toHaveBeenCalledOnce();
  });

  it("does not overlap state polling with a user update operation", async () => {
    const source = gateway({ state: "idle" });
    let finishCheck: ((state: AppUpdateState) => void) | undefined;
    vi.mocked(source.checkNow).mockImplementationOnce(
      () =>
        new Promise<AppUpdateState>((resolve) => {
          finishCheck = resolve;
        }),
    );
    const user = userEvent.setup();
    renderCenter(source, true);

    const check = await screen.findByRole("button", { name: "检查更新" });
    await waitFor(() => expect(source.getState).toHaveBeenCalledOnce());
    await user.click(check);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 1_100));

    expect(source.getState).toHaveBeenCalledOnce();
    finishCheck?.({ state: "up_to_date", trigger: "manual" });
    expect(await screen.findByText("当前已是最新版本")).toBeVisible();
  });

  it("maps native failures to fixed public copy without reflecting details", async () => {
    const source = gateway({ state: "idle" });
    vi.mocked(source.checkNow).mockRejectedValueOnce(
      new Error("token=private-update-secret path=/Users/private/update"),
    );
    const user = userEvent.setup();
    renderCenter(source, true);

    await user.click(await screen.findByRole("button", { name: "检查更新" }));

    expect(
      await screen.findByText("暂时无法读取或操作 App 更新，请稍后重试。"),
    ).toBeVisible();
    expect(document.body).not.toHaveTextContent("private-update-secret");
    expect(document.body).not.toHaveTextContent("/Users/private/update");
  });
});
