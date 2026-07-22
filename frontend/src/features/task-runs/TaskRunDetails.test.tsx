import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  TaskEvent,
  TaskProjectionRequestOptions,
  TaskProjectionSource,
  TaskSnapshot,
} from "../../api/control-plane/task-projections";
import { taskProjectionKeys } from "../../api/control-plane/task-projections";
import type {
  TaskTargetPreview,
  TaskTargetPreviewSource,
} from "../../api/control-plane/task-target-previews";
import type {
  TaskTargetResultSnapshot,
  TaskTargetResultSource,
} from "../../api/control-plane/task-target-results";
import { TaskRunDetails } from "./TaskRunDetails";
import {
  TaskDiscoveryGatewayError,
  type TaskDiscoveryGateway,
  type TaskDiscoveryReceipt,
} from "./task-discovery";
import type {
  TaskRunControlGateway,
  TaskRunControlReceipt,
} from "./task-run-controls";

const TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const ATTEMPT_ID = "16fd2706-8baf-433b-82eb-8c7fada847da";
const ACTION_ID = "adff54bd-3571-44da-8acd-5ea15695e5e9";
const OTHER_TASK_ID = "fd4aa304-3d73-4fd2-af88-c2c9747c4168";
const TARGET_ID = "6fa459ea-ee8a-4ca4-894e-db77e160355e";

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

function discoveryReceipt(): TaskDiscoveryReceipt {
  return {
    taskId: TASK_ID,
    taskStatus: "discovering_targets",
    taskRevision: 2,
    lastEventSequence: 1,
    commandId: "16fd2706-8baf-433b-82eb-8c7fada847da",
    executionAttemptId: ATTEMPT_ID,
    commandStatus: "pending",
  };
}

function discoveryGateway(): TaskDiscoveryGateway {
  return { startDiscovery: vi.fn(async () => discoveryReceipt()) };
}

function idleSource(taskSnapshot: TaskSnapshot): TaskProjectionSource {
  return {
    getTask: vi.fn(async () => taskSnapshot),
    listTasks: vi.fn(async () => ({ items: [taskSnapshot], nextCursor: null })),
    streamTaskEvents: vi.fn(
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
}

function targetPreview(): TaskTargetPreview {
  return {
    taskId: TASK_ID,
    taskStatus: "awaiting_confirmation",
    taskRevision: 3,
    confirmationRevision: 3,
    lastEventSequence: 2,
    pageRevision: 1,
    action: "browse",
    messageTemplate: null,
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

function targetResults(): TaskTargetResultSnapshot {
  return {
    taskId: TASK_ID,
    taskStatus: "partially_succeeded",
    taskRevision: 9,
    lastEventSequence: 8,
    items: [
      {
        targetId: TARGET_ID,
        ordinal: 1,
        displayName: "评论成功目标",
        publicHandle: "success.target",
        resultStatus: "succeeded",
        evidence: "comment_confirmed",
        actionId: ACTION_ID,
        updatedAt: "2026-07-18T15:33:00Z",
      },
      {
        targetId: "d9428888-122b-41aa-8fc2-0a5e810a4d18",
        ordinal: 2,
        displayName: "用户排除目标",
        publicHandle: null,
        resultStatus: "skipped",
        evidence: "user_excluded",
        actionId: null,
        updatedAt: "2026-07-18T15:33:00Z",
      },
      {
        targetId: "599605a4-f893-4aec-a45b-a1082b672348",
        ordinal: 3,
        displayName: "登录失效目标",
        publicHandle: "failed.target",
        resultStatus: "failed",
        evidence: "login_required",
        actionId: "5dff6a11-2962-452d-8463-cc852a0c3009",
        updatedAt: "2026-07-18T15:34:00Z",
      },
      {
        targetId: "118dfbba-c01d-4b16-bff7-26aa8f38b9ef",
        ordinal: 4,
        displayName: "结果待确认目标",
        publicHandle: "uncertain.target",
        resultStatus: "outcome_uncertain",
        evidence: "final_state_unconfirmed",
        actionId: "ce5cae75-b64d-411a-969e-0ac7cba79204",
        updatedAt: "2026-07-18T15:35:00Z",
      },
    ],
  };
}

function targetResultSource(): TaskTargetResultSource {
  return { getResults: vi.fn(async () => targetResults()) };
}

function renderDetails(
  taskSource = source(),
  controlGateway = gateway(),
  taskTargetPreviewSource = targetSource(),
  taskTargetResultSource = targetResultSource(),
  taskDiscoveryGateway = discoveryGateway(),
  onOpenPlatformSession = () => undefined,
  onPlatformLoginRequired = () => undefined,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    taskSource,
    controlGateway,
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <TaskRunDetails
          taskId={TASK_ID}
          taskSource={taskSource}
          controlGateway={controlGateway}
          discoveryGateway={taskDiscoveryGateway}
          taskTargetPreviewSource={taskTargetPreviewSource}
          taskTargetResultSource={taskTargetResultSource}
          onBack={() => undefined}
          onOpenPlatformSession={onOpenPlatformSession}
          onPlatformLoginRequired={onPlatformLoginRequired}
        />
      </QueryClientProvider>,
    ),
  };
}

describe("Task run details", () => {
  it("starts discovery from the real draft page with a stable idempotency key", async () => {
    const taskDiscoveryGateway = discoveryGateway();
    const user = userEvent.setup();
    renderDetails(
      idleSource(snapshot({ status: "draft", revision: 1, lastEventSequence: 0 })),
      gateway(),
      targetSource(),
      targetResultSource(),
      taskDiscoveryGateway,
    );

    await user.click(await screen.findByRole("button", { name: "开始目标发现" }));
    await waitFor(() =>
      expect(taskDiscoveryGateway.startDiscovery).toHaveBeenCalledTimes(1),
    );
    const [taskId, key] = vi.mocked(taskDiscoveryGateway.startDiscovery).mock.calls[0] ?? [];
    expect(taskId).toBe(TASK_ID);
    expect(key).toMatch(/^task:discover:start:[0-9a-f-]{36}$/u);
    expect(
      await screen.findByText("目标发现命令已提交，等待 Executor 返回候选"),
    ).toBeVisible();
  });

  it("reuses the discovery intent and opens platform repair after a safe rejection", async () => {
    const startDiscovery = vi
      .fn<TaskDiscoveryGateway["startDiscovery"]>()
      .mockRejectedValueOnce(new TaskDiscoveryGatewayError("discovery_rejected", false))
      .mockResolvedValueOnce(discoveryReceipt());
    const onOpenPlatformSession = vi.fn();
    const onPlatformLoginRequired = vi.fn();
    const user = userEvent.setup();
    renderDetails(
      idleSource(snapshot({ status: "awaiting_human", revision: 4 })),
      gateway(),
      targetSource(),
      targetResultSource(),
      { startDiscovery },
      onOpenPlatformSession,
      onPlatformLoginRequired,
    );

    await user.click(await screen.findByRole("button", { name: "开始目标发现" }));
    expect(
      await screen.findByText(
        "当前平台登录或任务状态尚未满足目标发现条件，请先处理平台状态后重试",
      ),
    ).toBeVisible();
    expect(onPlatformLoginRequired).toHaveBeenCalledOnce();
    await user.click(screen.getAllByRole("button", { name: "打开平台状态" })[0]!);
    expect(onOpenPlatformSession).toHaveBeenCalledOnce();

    const firstKey = startDiscovery.mock.calls[0]?.[1];
    await user.click(screen.getByRole("button", { name: "开始目标发现" }));
    await waitFor(() => expect(startDiscovery).toHaveBeenCalledTimes(2));
    expect(startDiscovery.mock.calls[1]?.[1]).toBe(firstKey);
  });

  it("requests one automatic login launch only when discovery enters the waiting state", async () => {
    let emitEvent: ((event: TaskEvent) => void) | null = null;
    const taskSource: TaskProjectionSource = {
      getTask: vi.fn(async () =>
        snapshot({
          status: "discovering_targets",
          revision: 2,
          lastEventSequence: 1,
        }),
      ),
      listTasks: vi.fn(async () => ({ items: [], nextCursor: null })),
      streamTaskEvents: vi.fn(
        async (_taskId, _afterSequence, onEvent, options = {}) => {
          emitEvent = onEvent;
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
    const onPlatformLoginRequired = vi.fn();
    renderDetails(
      taskSource,
      gateway(),
      targetSource(),
      targetResultSource(),
      discoveryGateway(),
      () => undefined,
      onPlatformLoginRequired,
    );

    expect((await screen.findAllByText("发现目标中")).length).toBeGreaterThanOrEqual(1);
    await waitFor(() => expect(emitEvent).not.toBeNull());
    expect(onPlatformLoginRequired).not.toHaveBeenCalled();
    await act(async () => {
      emitEvent?.({
        taskId: TASK_ID,
        sequence: 1,
        eventVersion: "1.0",
        eventType: "task.discovery_started",
        taskRevision: 2,
        taskStatus: "discovering_targets",
        executionAttemptId: ATTEMPT_ID,
        actionId: null,
        progressPercent: null,
        occurredAt: "2026-07-18T15:32:00Z",
        recordedAt: "2026-07-18T15:32:00Z",
        message: "Target discovery started",
      });
      emitEvent?.({
        taskId: TASK_ID,
        sequence: 2,
        eventVersion: "1.0",
        eventType: "task.awaiting_platform_login",
        taskRevision: 3,
        taskStatus: "awaiting_platform_login",
        executionAttemptId: ATTEMPT_ID,
        actionId: null,
        progressPercent: null,
        occurredAt: "2026-07-18T15:33:00Z",
        recordedAt: "2026-07-18T15:33:00Z",
        message: "Platform login is required",
      });
    });

    await waitFor(() => expect(onPlatformLoginRequired).toHaveBeenCalledOnce());
    expect(screen.getAllByText("等待平台登录").length).toBeGreaterThanOrEqual(1);
  });

  it("does not relaunch login when an existing waiting task is opened after App restart", async () => {
    const onPlatformLoginRequired = vi.fn();
    renderDetails(
      idleSource(snapshot({ status: "awaiting_platform_login", revision: 3 })),
      gateway(),
      targetSource(),
      targetResultSource(),
      discoveryGateway(),
      () => undefined,
      onPlatformLoginRequired,
    );

    expect((await screen.findAllByText("等待平台登录")).length).toBeGreaterThanOrEqual(1);
    expect(onPlatformLoginRequired).not.toHaveBeenCalled();
  });

  it("shows an explicit device single-task message for an Installation conflict", async () => {
    const taskDiscoveryGateway: TaskDiscoveryGateway = {
      startDiscovery: vi.fn(async () => {
        throw new TaskDiscoveryGatewayError("installation_busy", false);
      }),
    };
    const user = userEvent.setup();
    renderDetails(
      idleSource(snapshot({ status: "draft", revision: 1, lastEventSequence: 0 })),
      gateway(),
      targetSource(),
      targetResultSource(),
      taskDiscoveryGateway,
    );

    await user.click(await screen.findByRole("button", { name: "开始目标发现" }));
    expect(
      await screen.findByText("当前设备已有任务正在运行，请先完成或终止该任务后再试"),
    ).toBeVisible();
  });

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
          discoveryGateway={discoveryGateway()}
          taskTargetPreviewSource={previewSource}
          taskTargetResultSource={targetResultSource()}
          onBack={() => undefined}
          onOpenPlatformSession={() => undefined}
          onPlatformLoginRequired={() => undefined}
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
    expect(screen.getByText("评论成功目标")).toBeVisible();
    expect(screen.getByText("平台页面已确认评论成功", { exact: false })).toBeVisible();
    expect(screen.getByText("跳过")).toBeVisible();
    expect(screen.getByText("失败")).toBeVisible();
    expect(screen.getByText("结果不确定")).toBeVisible();
    expect(screen.getByText("已发送，但平台最终状态无法确认", { exact: false })).toBeVisible();
    expect(document.body).not.toHaveTextContent(/产品登录|注册账号|账号登录/);
  });

  it("refetches an early empty target result after terminal events reach the page", async () => {
    const terminalEvents: readonly TaskEvent[] = [
      {
        ...EVENTS[1]!,
        sequence: 1,
        eventType: "step.completed",
        taskRevision: 2,
        progressPercent: null,
      },
      {
        ...EVENTS[0]!,
        sequence: 2,
        eventType: "task.completed",
        taskRevision: 3,
        taskStatus: "succeeded",
        actionId: null,
      },
    ];
    const taskSource: TaskProjectionSource = {
      getTask: vi.fn(async () =>
        snapshot({ status: "running", revision: 1, lastEventSequence: 0 }),
      ),
      listTasks: vi.fn(async () => ({ items: [], nextCursor: null })),
      streamTaskEvents: vi.fn(async (_taskId, afterSequence, onEvent) => {
        expect(afterSequence).toBe(0);
        terminalEvents.forEach(onEvent);
        return { lastSequence: 2, terminal: true };
      }),
    };
    const completeResults = targetResults();
    const getResults = vi
      .fn<TaskTargetResultSource["getResults"]>()
      .mockResolvedValueOnce({
        taskId: TASK_ID,
        taskStatus: "running",
        taskRevision: 1,
        lastEventSequence: 0,
        items: [],
      })
      .mockResolvedValue({
        ...completeResults,
        taskStatus: "succeeded",
        taskRevision: 3,
        lastEventSequence: 2,
      });

    renderDetails(
      taskSource,
      gateway(),
      targetSource(),
      { getResults },
    );

    expect(await screen.findByText("任务已进入终态，控制按钮已关闭")).toBeVisible();
    await waitFor(() => expect(getResults).toHaveBeenCalledTimes(2));
    expect(screen.getByText("评论成功目标")).toBeVisible();
    expect(screen.getByText("用户排除目标")).toBeVisible();
  });

  it("does not lose the terminal result refresh while the initial result request is pending", async () => {
    const terminalEvents: readonly TaskEvent[] = [
      {
        ...EVENTS[1]!,
        sequence: 1,
        eventType: "step.completed",
        taskRevision: 2,
        progressPercent: null,
      },
      {
        ...EVENTS[0]!,
        sequence: 2,
        eventType: "task.completed",
        taskRevision: 3,
        taskStatus: "succeeded",
        actionId: null,
      },
    ];
    let releaseInitialResult: (() => void) | undefined;
    let publishTerminalEvents: (() => void) | undefined;
    const initialResult = new Promise<TaskTargetResultSnapshot>((resolve) => {
      releaseInitialResult = () =>
        resolve({
          taskId: TASK_ID,
          taskStatus: "running",
          taskRevision: 1,
          lastEventSequence: 0,
          items: [],
        });
    });
    const terminalStream = new Promise<{ lastSequence: number; terminal: boolean }>(
      (resolve) => {
        publishTerminalEvents = () => resolve({ lastSequence: 2, terminal: true });
      },
    );
    const taskSource: TaskProjectionSource = {
      getTask: vi.fn(async () =>
        snapshot({ status: "running", revision: 1, lastEventSequence: 0 }),
      ),
      listTasks: vi.fn(async () => ({ items: [], nextCursor: null })),
      streamTaskEvents: vi.fn(async (_taskId, afterSequence, onEvent) => {
        expect(afterSequence).toBe(0);
        const summary = await terminalStream;
        terminalEvents.forEach(onEvent);
        return summary;
      }),
    };
    const completeResults = targetResults();
    const getResults = vi
      .fn<TaskTargetResultSource["getResults"]>()
      .mockImplementationOnce(async () => initialResult)
      .mockResolvedValue({
        ...completeResults,
        taskStatus: "succeeded",
        taskRevision: 3,
        lastEventSequence: 2,
      });

    renderDetails(taskSource, gateway(), targetSource(), { getResults });

    await waitFor(() => expect(getResults).toHaveBeenCalledTimes(1));
    publishTerminalEvents?.();
    expect(await screen.findByText("任务已进入终态，控制按钮已关闭")).toBeVisible();
    releaseInitialResult?.();

    await waitFor(() => expect(getResults).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("评论成功目标")).toBeVisible();
    expect(screen.getByText("用户排除目标")).toBeVisible();
  });

  it("refreshes results from the authoritative Task watermark when the event stream lags", async () => {
    const taskSource = source();
    vi.mocked(taskSource.getTask)
      .mockResolvedValueOnce(
        snapshot({ status: "running", revision: 1, lastEventSequence: 0 }),
      )
      .mockResolvedValue(
        snapshot({ status: "succeeded", revision: 9, lastEventSequence: 8 }),
      );
    const completeResults = targetResults();
    const getResults = vi
      .fn<TaskTargetResultSource["getResults"]>()
      .mockResolvedValueOnce({
        taskId: TASK_ID,
        taskStatus: "running",
        taskRevision: 1,
        lastEventSequence: 0,
        items: [],
      })
      .mockResolvedValue({
        ...completeResults,
        taskStatus: "succeeded",
        taskRevision: 9,
        lastEventSequence: 8,
      });
    const rendered = renderDetails(
      taskSource,
      gateway(),
      targetSource(),
      { getResults },
    );

    await waitFor(() => expect(getResults).toHaveBeenCalledTimes(1));
    await rendered.queryClient.refetchQueries({
      queryKey: taskProjectionKeys.detail(TASK_ID),
    });

    expect(await screen.findByText("任务已进入终态，控制按钮已关闭")).toBeVisible();
    await waitFor(() => expect(getResults).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("评论成功目标")).toBeVisible();
    expect(screen.getByText("用户排除目标")).toBeVisible();
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

  it("keeps the last authoritative snapshot and emergency stop during a background outage", async () => {
    const taskSource = source();
    vi.mocked(taskSource.getTask)
      .mockResolvedValueOnce(snapshot())
      .mockRejectedValueOnce(new Error("offline"));
    const rendered = renderDetails(taskSource);

    expect(await screen.findByRole("button", { name: "紧急停止" })).toBeEnabled();
    await rendered.queryClient.refetchQueries({
      queryKey: taskProjectionKeys.detail(TASK_ID),
    });
    await waitFor(() =>
      expect(rendered.queryClient.getQueryState(taskProjectionKeys.detail(TASK_ID))?.status).toBe(
        "error",
      ),
    );

    expect(screen.getByRole("heading", { name: "任务运行详情" })).toBeVisible();
    expect(screen.getByRole("button", { name: "紧急停止" })).toBeEnabled();
    expect(screen.queryByText("任务详情暂时不可用")).not.toBeInTheDocument();
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
