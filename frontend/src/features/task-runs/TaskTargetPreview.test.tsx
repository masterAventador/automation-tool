import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  TaskTargetPreviewSourceError,
  type TaskTargetPreview,
  type TaskTargetPreviewSource,
} from "../../api/control-plane/task-target-previews";
import { TaskTargetPreviewPanel } from "./TaskTargetPreview";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const ELIGIBLE_ONE = "6fa459ea-ee8a-4ca4-894e-db77e160355e";
const ELIGIBLE_TWO = "e5be4b4e-cc36-4fd5-bbb0-d206bcf1f092";
const DUPLICATE = "f6f5734b-51e2-4fd8-ae19-922f6e2db57d";
const BLACKLISTED = "2a1e3e24-95fc-42e8-a4f4-a74e6d83dfda";

function preview(overrides: Partial<TaskTargetPreview> = {}): TaskTargetPreview {
  return {
    taskId: TASK_ID,
    taskStatus: "awaiting_confirmation",
    taskRevision: 3,
    lastEventSequence: 2,
    pageRevision: 1,
    selectedTargetCount: 2,
    userExcludedTargetCount: 0,
    confirmed: false,
    confirmedAt: null,
    items: [
      {
        targetId: ELIGIBLE_ONE,
        ordinal: 1,
        displayName: "目标甲",
        publicHandle: "target.alpha",
        source: "general_search_author",
        disposition: "eligible",
        userExcluded: false,
        selected: true,
      },
      {
        targetId: ELIGIBLE_TWO,
        ordinal: 2,
        displayName: "目标乙",
        publicHandle: null,
        source: "general_search_author",
        disposition: "eligible",
        userExcluded: false,
        selected: true,
      },
      {
        targetId: DUPLICATE,
        ordinal: 3,
        displayName: "历史目标",
        publicHandle: "target.history",
        source: "general_search_author",
        disposition: "duplicate_in_history",
        userExcluded: false,
        selected: false,
      },
      {
        targetId: BLACKLISTED,
        ordinal: 4,
        displayName: "黑名单目标",
        publicHandle: "target.blocked",
        source: "general_search_author",
        disposition: "blacklisted",
        userExcluded: false,
        selected: false,
      },
    ],
    nextCursor: null,
    ...overrides,
  };
}

function source(initial = preview()): TaskTargetPreviewSource {
  return {
    getPreview: vi.fn(async () => initial),
    replaceExclusions: vi.fn(async (request) =>
      preview({
        taskRevision: request.expectedTaskRevision + 1,
        lastEventSequence: 3,
        selectedTargetCount: 1,
        userExcludedTargetCount: 1,
        items: preview().items.map((item) =>
          item.targetId === ELIGIBLE_ONE
            ? { ...item, userExcluded: true, selected: false }
            : item,
        ),
      }),
    ),
    confirm: vi.fn(async (request) =>
      preview({
        taskStatus: "queued",
        taskRevision: request.expectedTaskRevision + 1,
        lastEventSequence: 4,
        confirmed: true,
        confirmedAt: "2026-07-20T01:30:00Z",
      }),
    ),
  };
}

function renderPreview(targetSource = source()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    targetSource,
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <TaskTargetPreviewPanel taskId={TASK_ID} source={targetSource} />
      </QueryClientProvider>,
    ),
  };
}

describe("Task target preview panel", () => {
  it("shows the bounded summary, source, and policy dispositions without private IDs", async () => {
    renderPreview();

    expect(await screen.findByRole("heading", { name: "目标预览" })).toBeVisible();
    expect(screen.getByText("已发现 4 个目标")).toBeVisible();
    expect(screen.getByText("计划执行 2 个")).toBeVisible();
    expect(screen.getByText("策略拦截 2 个")).toBeVisible();
    expect(screen.getAllByText("抖音通用搜索作者")).toHaveLength(4);
    expect(screen.getByText("30 天内已触达")).toBeVisible();
    expect(screen.getByText("黑名单")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "选择目标 目标甲" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "策略已排除 历史目标" })).toBeDisabled();
    expect(document.body).not.toHaveTextContent(ELIGIBLE_ONE);
    expect(document.body).not.toHaveTextContent(BLACKLISTED);
  });

  it("replaces exclusions through the source with the current revisions", async () => {
    const targetSource = source();
    const user = userEvent.setup();
    renderPreview(targetSource);

    await user.click(await screen.findByRole("checkbox", { name: "选择目标 目标甲" }));

    await waitFor(() => expect(targetSource.replaceExclusions).toHaveBeenCalledTimes(1));
    expect(vi.mocked(targetSource.replaceExclusions).mock.calls[0]?.[0]).toMatchObject({
      taskId: TASK_ID,
      pageRevision: 1,
      expectedTaskRevision: 3,
      excludedTargetIds: [ELIGIBLE_ONE],
      idempotencyKey: expect.stringMatching(/^task-targets:exclude:[0-9a-f-]{36}$/),
    });
    expect(await screen.findByText("本次排除 1 个")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "选择目标 目标甲" })).not.toBeChecked();
  });

  it("confirms the latest selection revision and closes further editing", async () => {
    const targetSource = source();
    const user = userEvent.setup();
    renderPreview(targetSource);

    await user.click(await screen.findByRole("button", { name: "确认执行" }));
    await user.click(screen.getByRole("button", { name: "确认目标" }));

    await waitFor(() => expect(targetSource.confirm).toHaveBeenCalledTimes(1));
    expect(vi.mocked(targetSource.confirm).mock.calls[0]?.[0]).toMatchObject({
      taskId: TASK_ID,
      pageRevision: 1,
      expectedTaskRevision: 3,
      idempotencyKey: expect.stringMatching(/^task-targets:confirm:[0-9a-f-]{36}$/),
    });
    expect(await screen.findByText("目标已确认，任务已进入执行队列")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "选择目标 目标甲" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled();
  });

  it("reloads a stale preview and never exposes private transport details", async () => {
    const targetSource = source();
    vi.mocked(targetSource.replaceExclusions).mockRejectedValueOnce(
      new TaskTargetPreviewSourceError("preview_stale", false),
    );
    vi.mocked(targetSource.getPreview)
      .mockResolvedValueOnce(preview())
      .mockResolvedValueOnce(preview({ taskRevision: 4 }));
    const user = userEvent.setup();
    renderPreview(targetSource);

    await user.click(await screen.findByRole("checkbox", { name: "选择目标 目标甲" }));

    expect(await screen.findByText("目标列表已变化，已重新加载最新版本")).toBeVisible();
    await waitFor(() => expect(targetSource.getPreview).toHaveBeenCalledTimes(2));
    expect(document.body).not.toHaveTextContent(/password=|cookie=|token=/i);
  });

  it("retries an uncertain exclusion with the same idempotency key", async () => {
    const targetSource = source();
    vi.mocked(targetSource.replaceExclusions)
      .mockRejectedValueOnce(new Error("password=private-transport-detail"))
      .mockResolvedValueOnce(
        preview({
          taskRevision: 4,
          lastEventSequence: 3,
          selectedTargetCount: 1,
          userExcludedTargetCount: 1,
          items: preview().items.map((item) =>
            item.targetId === ELIGIBLE_ONE
              ? { ...item, userExcluded: true, selected: false }
              : item,
          ),
        }),
      );
    const user = userEvent.setup();
    renderPreview(targetSource);

    const checkbox = await screen.findByRole("checkbox", { name: "选择目标 目标甲" });
    await user.click(checkbox);
    expect(
      await screen.findByText("目标选择结果暂时无法确认，请重新核对后重试"),
    ).toBeVisible();
    expect(document.body).not.toHaveTextContent("password=private-transport-detail");

    await user.click(checkbox);
    await waitFor(() => expect(targetSource.replaceExclusions).toHaveBeenCalledTimes(2));
    const firstKey = vi.mocked(targetSource.replaceExclusions).mock.calls[0]?.[0]
      .idempotencyKey;
    const secondKey = vi.mocked(targetSource.replaceExclusions).mock.calls[1]?.[0]
      .idempotencyKey;
    expect(firstKey).toBe(secondKey);
    expect(await screen.findByText("目标选择已保存")).toBeVisible();
  });

  it("allows cancelling every eligible target but blocks an empty confirmation", async () => {
    const targetSource = source();
    vi.mocked(targetSource.replaceExclusions).mockResolvedValueOnce(
      preview({
        taskRevision: 4,
        lastEventSequence: 3,
        selectedTargetCount: 0,
        userExcludedTargetCount: 2,
        items: preview().items.map((item) =>
          item.disposition === "eligible"
            ? { ...item, userExcluded: true, selected: false }
            : item,
        ),
      }),
    );
    const user = userEvent.setup();
    renderPreview(targetSource);

    await user.click(await screen.findByRole("button", { name: "全部取消" }));

    await waitFor(() => expect(targetSource.replaceExclusions).toHaveBeenCalledTimes(1));
    expect(
      vi.mocked(targetSource.replaceExclusions).mock.calls[0]?.[0].excludedTargetIds,
    ).toEqual([ELIGIBLE_ONE, ELIGIBLE_TWO]);
    expect(await screen.findByText("计划执行 0 个")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled();
  });
});
