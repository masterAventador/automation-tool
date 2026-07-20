import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  TaskEvent,
  TaskProjectionRequestOptions,
  TaskProjectionSource,
  TaskSnapshot,
} from "../../api/control-plane/task-projections";
import type {
  TaskTargetPreview,
  TaskTargetPreviewSource,
} from "../../api/control-plane/task-target-previews";
import { TaskRunDetails } from "./TaskRunDetails";
import type {
  TaskRunControlGateway,
  TaskRunControlReceipt,
} from "./task-run-controls";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const ATTEMPT_ID = "16fd2706-8baf-433b-82eb-8c7fada847da";
const ACTION_ID = "adff54bd-3571-44da-8acd-5ea15695e5e9";
const OTHER_TASK_ID = "fd4aa304-3d73-4fd2-af88-c2c9747c4168";

function snapshot(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    taskId: TASK_ID,
    status: "running",
    revision: 5,
    lastEventSequence: 2,
    createdAt: "2026-07-18T15:30:00Z",
    updatedAt: "2026-07-18T15:32:00Z",
    ...overrides,
  };
}

const EVENTS: readonly TaskEvent[] = [
  {
    taskId: TASK_ID,
    sequence: 1,
    eventVersion: "1.0",
    eventType: "task.started",
    taskRevision: 4,
    taskStatus: "running",
    executionAttemptId: ATTEMPT_ID,
    actionId: null,
    progressPercent: null,
    occurredAt: "2026-07-18T15:31:00Z",
    recordedAt: "2026-07-18T15:31:00Z",
    message: "Task started",
  },
  {
    taskId: TASK_ID,
    sequence: 2,
    eventVersion: "1.0",
    eventType: "step.progress",
    taskRevision: 5,
    taskStatus: "running",
    executionAttemptId: ATTEMPT_ID,
    actionId: ACTION_ID,
    progressPercent: 50,
    occurredAt: "2026-07-18T15:32:00Z",
    recordedAt: "2026-07-18T15:32:00Z",
    message: "Step is running",
  },
];

function source(taskSnapshot = snapshot()): TaskProjectionSource {
  return {
    getTask: vi.fn(async () => taskSnapshot),
    listTasks: vi.fn(async () => ({ items: [taskSnapshot], nextCursor: null })),
    streamTaskEvents: vi.fn(
      async (
        _taskId: string,
        afterSequence: number,
        onEvent: (event: TaskEvent) => void,
        options: TaskProjectionRequestOptions = {},
      ) => {
        expect(afterSequence).toBe(0);
        EVENTS.forEach(onEvent);
        return new Promise<{ lastSequence: number; terminal: boolean }>((resolve) => {
          options.signal?.addEventListener(
            "abort",
            () => resolve({ lastSequence: 2, terminal: false }),
            { once: true },
          );
        });
      },
    ),
  };
}

function receipt(commandType: TaskRunControlReceipt["commandType"]): TaskRunControlReceipt {
  return {
    commandId: "6fa459ea-ee8a-4ca4-894e-db77e160355e",
    taskId: TASK_ID,
    executionAttemptId: ATTEMPT_ID,
    sequence: 3,
    commandType,
    status: "pending",
  };
}

function gateway(): TaskRunControlGateway {
  return {
    pauseTask: vi.fn(async () => receipt("task.pause")),
    resumeTask: vi.fn(async () => receipt("task.resume")),
    cancelTask: vi.fn(async () => receipt("task.cancel")),
    emergencyStopTask: vi.fn(async () => receipt("task.emergency_stop")),
  };
}

function targetPreview(): TaskTargetPreview {
  return {
    taskId: TASK_ID,
    taskStatus: "awaiting_confirmation",
    taskRevision: 3,
    lastEventSequence: 2,
    pageRevision: 1,
    selectedTargetCount: 1,
    userExcludedTargetCount: 0,
    confirmed: false,
    confirmedAt: null,
    items: [
      {
        targetId: "6fa459ea-ee8a-4ca4-894e-db77e160355e",
        ordinal: 1,
        displayName: "任务详情候选",
        publicHandle: "details.candidate",
        source: "general_search_author",
        disposition: "eligible",
        userExcluded: false,
        selected: true,
      },
    ],
    nextCursor: null,
  };
}

function targetSource(): TaskTargetPreviewSource {
  return {
    getPreview: vi.fn(async () => targetPreview()),
    replaceExclusions: vi.fn(async () => targetPreview()),
    confirm: vi.fn(async () => targetPreview()),
  };
}

function renderDetails(
  taskSource = source(),
  controlGateway = gateway(),
  taskTargetPreviewSource = targetSource(),
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    taskSource,
    controlGateway,
    ...render(
      <QueryClientProvider client={queryClient}>
        <TaskRunDetails
          taskId={TASK_ID}
          taskSource={taskSource}
          controlGateway={controlGateway}
          taskTargetPreviewSource={taskTargetPreviewSource}
          onBack={() => undefined}
        />
      </QueryClientProvider>,
    ),
  };
}

describe("Task run details", () => {
  it("opens the target preview inside details only while confirmation is required", async () => {
    const previewSource = targetSource();
    renderDetails(
      source(snapshot({ status: "awaiting_confirmation", revision: 3 })),
      gateway(),
      previewSource,
    );

    expect(await screen.findByRole("heading", { name: "目标预览" })).toBeVisible();
    expect(screen.getByText("任务详情候选")).toBeVisible();
    expect(previewSource.getPreview).toHaveBeenCalledOnce();
  });

  it("does not carry an opened preview into another task that has not requested confirmation", async () => {
    const taskSource: TaskProjectionSource = {
      getTask: vi.fn(async (taskId) =>
        snapshot({
          taskId,
          status: taskId === TASK_ID ? "awaiting_confirmation" : "running",
          revision: 3,
          lastEventSequence: 0,
        }),
      ),
      listTasks: vi.fn(async () => ({ items: [], nextCursor: null })),
      streamTaskEvents: vi.fn<TaskProjectionSource["streamTaskEvents"]>(
        async (_taskId, afterSequence, _onEvent, options = {}) =>
          new Promise<{ lastSequence: number; terminal: boolean }>((resolve) => {
            options.signal?.addEventListener(
              "abort",
              () => resolve({ lastSequence: afterSequence, terminal: false }),
              { once: true },
            );
          }),
      ),
    };
    const previewSource = targetSource();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const details = (taskId: string) => (
      <QueryClientProvider client={queryClient}>
        <TaskRunDetails
          taskId={taskId}
          taskSource={taskSource}
          controlGateway={gateway()}
          taskTargetPreviewSource={previewSource}
          onBack={() => undefined}
        />
      </QueryClientProvider>
    );
    const rendered = render(details(TASK_ID));

    expect(await screen.findByRole("heading", { name: "目标预览" })).toBeVisible();
    rendered.rerender(details(OTHER_TASK_ID));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "目标预览" })).not.toBeInTheDocument(),
    );
    expect(previewSource.getPreview).toHaveBeenCalledTimes(1);
  });

  it("shows authoritative status, progress, history, and scoped Action results", async () => {
    renderDetails();

    expect(await screen.findByRole("heading", { name: "任务运行详情" })).toBeVisible();
    expect(screen.getAllByText("运行中").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("50%")).toBeVisible();
    expect(await screen.findByText("任务开始")).toBeVisible();
    expect(screen.getAllByText("步骤进度").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(`目标 ${ACTION_ID.slice(-8)}`)).toBeVisible();
    expect(screen.getByText("进行中")).toBeVisible();
    expect(document.body).not.toHaveTextContent(/产品登录|注册账号|账号登录/);
  });

  it("submits pause from its real page control without claiming it is already paused", async () => {
    const controlGateway = gateway();
    const user = userEvent.setup();
    renderDetails(source(), controlGateway);

    expect((await screen.findAllByText("步骤进度")).length).toBeGreaterThanOrEqual(1);
    await user.click(screen.getByRole("button", { name: /暂.*停/ }));

    await waitFor(() => expect(controlGateway.pauseTask).toHaveBeenCalledTimes(1));
    const [taskId, key] = vi.mocked(controlGateway.pauseTask).mock.calls[0] ?? [];
    expect(taskId).toBe(TASK_ID);
    expect(key).toMatch(/^task-run:pause:[0-9a-f-]{36}$/);
    expect(await screen.findByText("暂停命令已提交，等待 Executor 确认")).toBeVisible();
    expect(screen.getAllByText("运行中").length).toBeGreaterThanOrEqual(1);
  });

  it("exposes only state-compatible controls and confirms destructive commands", async () => {
    const controlGateway = gateway();
    const user = userEvent.setup();
    const firstRender = renderDetails(
      source(snapshot({ status: "paused" })),
      controlGateway,
    );

    expect(await screen.findByRole("button", { name: /恢.*复/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /暂.*停/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /恢.*复/ }));
    expect(controlGateway.resumeTask).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "取消任务" }));
    await user.click(screen.getByRole("button", { name: "确认取消" }));
    expect(controlGateway.cancelTask).toHaveBeenCalledTimes(1);

    firstRender.unmount();
    renderDetails(source(snapshot({ status: "paused" })), controlGateway);
    await screen.findByRole("heading", { name: "任务运行详情" });
    await user.click(screen.getByRole("button", { name: "紧急停止" }));
    await user.click(screen.getByRole("button", { name: "确认紧停" }));
    expect(controlGateway.emergencyStopTask).toHaveBeenCalledTimes(1);
  });

  it("stops on a timeline gap and restarts only from the explicit safe retry", async () => {
    const streamTaskEvents = vi
      .fn<TaskProjectionSource["streamTaskEvents"]>()
      .mockImplementationOnce(async (_taskId, _afterSequence, onEvent) => {
        onEvent(EVENTS[0]!);
        onEvent({ ...EVENTS[1]!, sequence: 3 });
        return { lastSequence: 3, terminal: false };
      })
      .mockImplementationOnce(async (_taskId, afterSequence, onEvent, options = {}) => {
        expect(afterSequence).toBe(0);
        EVENTS.forEach(onEvent);
        return new Promise((resolve) => {
          options.signal?.addEventListener(
            "abort",
            () => resolve({ lastSequence: 2, terminal: false }),
            { once: true },
          );
        });
      });
    const taskSource: TaskProjectionSource = {
      getTask: vi.fn(async () => snapshot()),
      listTasks: vi.fn(async () => ({ items: [snapshot()], nextCursor: null })),
      streamTaskEvents,
    };
    const user = userEvent.setup();
    renderDetails(taskSource);

    expect(await screen.findByText("事件时间线暂时中断")).toBeVisible();
    expect(screen.getByText("任务开始")).toBeVisible();
    expect(screen.queryByText("password=secret")).not.toBeInTheDocument();
    expect(streamTaskEvents).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新加载时间线" }));
    await waitFor(() => expect(streamTaskEvents).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("50%")).toBeVisible();
  });

  it("retries an uncertain control with the same idempotency key and no private error", async () => {
    const controlGateway = gateway();
    vi.mocked(controlGateway.pauseTask)
      .mockRejectedValueOnce(new Error("password=secret"))
      .mockResolvedValueOnce(receipt("task.pause"));
    const user = userEvent.setup();
    renderDetails(source(), controlGateway);

    const pause = await screen.findByRole("button", { name: /暂.*停/ });
    await user.click(pause);
    expect(
      await screen.findByText("命令结果暂时无法确认，请查看权威状态后重试"),
    ).toBeVisible();
    expect(document.body).not.toHaveTextContent("password=secret");

    await user.click(pause);
    await waitFor(() => expect(controlGateway.pauseTask).toHaveBeenCalledTimes(2));
    const firstKey = vi.mocked(controlGateway.pauseTask).mock.calls[0]?.[1];
    const secondKey = vi.mocked(controlGateway.pauseTask).mock.calls[1]?.[1];
    expect(firstKey).toBe(secondKey);
    expect(await screen.findByText("暂停命令已提交，等待 Executor 确认")).toBeVisible();
  });

  it("closes every control after an authoritative terminal snapshot", async () => {
    renderDetails(source(snapshot({ status: "cancelled" })));

    expect(await screen.findByText("任务已进入终态，控制按钮已关闭")).toBeVisible();
    for (const name of [/暂.*停/, /恢.*复/, "取消任务", "紧急停止"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
  });
});
