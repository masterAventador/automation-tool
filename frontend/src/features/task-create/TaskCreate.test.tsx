import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TaskCreate } from "./TaskCreate";
import type { TaskCreationGateway } from "./task-creation-gateway";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

function gateway(): TaskCreationGateway {
  return {
    createDouyinSearchExposureTask: vi.fn(async () => ({
      taskId: TASK_ID,
      status: "draft" as const,
      revision: 1,
      lastEventSequence: 0,
      createdAt: "2026-07-18T15:30:00Z",
      updatedAt: "2026-07-18T15:30:00Z",
    })),
  };
}

function renderForm(taskCreationGateway = gateway()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    taskCreationGateway,
    ...render(
      <QueryClientProvider client={queryClient}>
        <TaskCreate gateway={taskCreationGateway} onCreated={() => undefined} />
      </QueryClientProvider>,
    ),
  };
}

describe("Douyin search exposure Task creation", () => {
  it("submits one exact safe definition through the production-shaped gateway", async () => {
    const user = userEvent.setup();
    const { taskCreationGateway } = renderForm();

    await user.type(screen.getByLabelText("搜索关键词"), "新能源汽车");
    await user.click(screen.getByLabelText("动作"));
    await user.click(await screen.findByText("评论"));
    await user.type(screen.getByLabelText("评论或私信模板"), "内容很有启发");
    await user.clear(screen.getByLabelText("单任务目标上限"));
    await user.type(screen.getByLabelText("单任务目标上限"), "12");
    await user.click(screen.getByRole("button", { name: "创建任务" }));

    expect(taskCreationGateway.createDouyinSearchExposureTask).toHaveBeenCalledTimes(1);
    const [definition, key] = vi.mocked(
      taskCreationGateway.createDouyinSearchExposureTask,
    ).mock.calls[0] ?? [];
    expect(definition).toMatchObject({
      template: "douyin.search_exposure.v1",
      searchKeyword: "新能源汽车",
      action: "comment",
      messageTemplate: "内容很有启发",
      targetLimit: 12,
      previewRequired: true,
      finalConfirmationRequired: true,
    });
    expect(key).toMatch(/^task:create:douyin-search:[0-9a-f-]{36}$/);
    expect(await screen.findByText(`任务已创建：${TASK_ID}`)).toBeVisible();
  });

  it("keeps preview and final confirmation mandatory and validates before invoke", async () => {
    const user = userEvent.setup();
    const { taskCreationGateway } = renderForm();

    expect(screen.getByText("目标预览固定开启")).toBeVisible();
    expect(screen.getByText("执行前最终确认固定开启")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "创建任务" }));

    await waitFor(() => expect(screen.getByText("请输入搜索关键词")).toBeVisible());
    expect(taskCreationGateway.createDouyinSearchExposureTask).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent(/产品登录|注册账号|账号登录/);
  });
});
