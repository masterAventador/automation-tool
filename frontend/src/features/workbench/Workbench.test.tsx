import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  TaskEvent,
  TaskListPage,
  TaskEventStreamSummary,
  TaskProjectionRequestOptions,
  TaskProjectionSource,
  TaskSnapshot,
} from "../../api/control-plane/task-projections";
import { Workbench } from "./Workbench";
import type {
  EmergencyStopReceipt,
  WorkbenchGateway,
  WorkbenchRuntimeStatus,
} from "./workbench-gateway";

const RUNNING_TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const COMPLETED_TASK_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

function task(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    taskId: RUNNING_TASK_ID,
    status: "running",
    revision: 5,
    lastEventSequence: 2,
    createdAt: "2026-07-18T14:00:00Z",
    updatedAt: "2026-07-18T14:05:00Z",
    ...overrides,
  };
}

function taskPage(): TaskListPage {
  return {
    items: [
      task(),
      task({
        taskId: COMPLETED_TASK_ID,
        status: "succeeded",
        revision: 6,
        lastEventSequence: 5,
        createdAt: "2026-07-18T13:00:00Z",
        updatedAt: "2026-07-18T13:08:00Z",
      }),
    ],
    nextCursor: null,
  };
}

function source(): TaskProjectionSource {
  return {
    getTask: vi.fn(async () => task()),
    listTasks: vi.fn(async () => taskPage()),
    streamTaskEvents: vi.fn(
      async (
        _taskId: string,
        afterSequence: number,
        _onEvent: (event: TaskEvent) => void,
        options: TaskProjectionRequestOptions = {},
      ): Promise<TaskEventStreamSummary> =>
        new Promise<TaskEventStreamSummary>((resolve) => {
          options.signal?.addEventListener(
            "abort",
            () => resolve({ lastSequence: afterSequence, terminal: false }),
            { once: true },
          );
        }),
    ),
  };
}

function gateway(): WorkbenchGateway {
  return {
    getRuntimeStatus: vi.fn(async (): Promise<WorkbenchRuntimeStatus> => ({
      controlPlaneStatus: "ready",
      executorStatus: "online",
      executorLastHeartbeatAt: "2026-07-18T14:05:01Z",
    })),
    emergencyStopTask: vi.fn(async (taskId): Promise<EmergencyStopReceipt> => ({
      commandId: "16fd2706-8baf-433b-82eb-8c7fada847da",
      taskId,
      executionAttemptId: "adff54bd-3571-44da-8acd-5ea15695e5e9",
      sequence: 2,
      commandType: "task.emergency_stop",
      status: "pending",
    })),
  };
}

function renderWorkbench(taskSource = source(), workbenchGateway = gateway()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    taskSource,
    workbenchGateway,
    ...render(
      <QueryClientProvider client={queryClient}>
        <Workbench taskSource={taskSource} gateway={workbenchGateway} />
      </QueryClientProvider>,
    ),
  };
}

describe("RPA workbench", () => {
  it("renders authoritative runtime, current Task, metrics, and recent Tasks", async () => {
    renderWorkbench();

    expect(await screen.findByText("Control Plane 已连接")).toBeVisible();
    expect(screen.getByText("Executor 在线")).toBeVisible();
    expect(screen.getByRole("heading", { name: "当前任务" })).toBeVisible();
    expect(screen.getAllByText("运行中")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "最近任务" })).toBeVisible();
    expect(screen.getByText(COMPLETED_TASK_ID)).toBeVisible();
    expect(screen.getByText("已成功")).toBeVisible();
    const today = screen.getByText("今日任务").closest(".ant-statistic");
    expect(today).toBeVisible();
    expect(today).toHaveTextContent("2");
    expect(document.body).not.toHaveTextContent(/产品登录|注册账号|账号登录/);
  });

  it("uses the newer live projection in the recent Task summary", async () => {
    const taskSource = source();
    vi.mocked(taskSource.listTasks).mockResolvedValue({
      items: [task({ status: "draft", revision: 1, lastEventSequence: 0 })],
      nextCursor: null,
    });
    renderWorkbench(taskSource);

    expect((await screen.findAllByText("运行中")).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("草稿")).not.toBeInTheDocument();
  });

  it("confirms and sends global emergency stop through the current Task gateway", async () => {
    const workbenchGateway = gateway();
    const user = userEvent.setup();
    renderWorkbench(source(), workbenchGateway);

    await screen.findByText("Executor 在线");
    await user.click(screen.getByRole("button", { name: "全局紧急停止" }));
    await user.click(screen.getByRole("button", { name: "确认紧停" }));

    expect(workbenchGateway.emergencyStopTask).toHaveBeenCalledTimes(1);
    const [taskId, idempotencyKey] = vi.mocked(
      workbenchGateway.emergencyStopTask,
    ).mock.calls[0] ?? [];
    expect(taskId).toBe(RUNNING_TASK_ID);
    expect(idempotencyKey).toMatch(/^workbench:emergency-stop:[0-9a-f-]{36}$/);
    expect(await screen.findByText("紧停命令已提交")).toBeVisible();
  });

  it("reuses one emergency-stop idempotency key when confirmation is retried", async () => {
    const workbenchGateway = gateway();
    vi.mocked(workbenchGateway.emergencyStopTask)
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce({
        commandId: "16fd2706-8baf-433b-82eb-8c7fada847da",
        taskId: RUNNING_TASK_ID,
        executionAttemptId: "adff54bd-3571-44da-8acd-5ea15695e5e9",
        sequence: 2,
        commandType: "task.emergency_stop",
        status: "pending",
      });
    const user = userEvent.setup();
    renderWorkbench(source(), workbenchGateway);

    await screen.findByText("Executor 在线");
    for (const notice of ["紧停结果暂时无法确认，请查看任务状态", "紧停命令已提交"]) {
      await user.click(screen.getByRole("button", { name: "全局紧急停止" }));
      await user.click(screen.getByRole("button", { name: "确认紧停" }));
      expect(await screen.findByText(notice)).toBeVisible();
    }

    const calls = vi.mocked(workbenchGateway.emergencyStopTask).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0]?.[1]).toBe(calls[1]?.[1]);
  });

  it("shows a fixed retryable state without leaking gateway failures", async () => {
    const workbenchGateway = gateway();
    vi.mocked(workbenchGateway.getRuntimeStatus).mockRejectedValue(
      new Error("password=private-runtime-secret"),
    );
    const taskSource = source();
    vi.mocked(taskSource.listTasks).mockRejectedValue(
      new Error("Bearer private-task-secret"),
    );
    const user = userEvent.setup();
    renderWorkbench(taskSource, workbenchGateway);

    expect(await screen.findByText("工作台数据暂时不可用")).toBeVisible();
    expect(document.body).not.toHaveTextContent(/private-runtime-secret|private-task-secret/);
    await user.click(screen.getByRole("button", { name: "重新加载工作台" }));
    expect(workbenchGateway.getRuntimeStatus).toHaveBeenCalledTimes(2);
    expect(taskSource.listTasks).toHaveBeenCalledTimes(2);
  });
});
