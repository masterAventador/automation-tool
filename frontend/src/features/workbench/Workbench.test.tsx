import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
  WorkbenchMetrics,
  WorkbenchRuntimeStatus,
} from "./workbench-gateway";

const RUNNING_TASK_ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const COMPLETED_TASK_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

function todayAt(hour: number, minute: number): string {
  const value = new Date();
  value.setHours(hour, minute, 0, 0);
  return value.toISOString();
}

function task(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    taskId: RUNNING_TASK_ID,
    status: "running",
    revision: 5,
    lastEventSequence: 2,
    createdAt: todayAt(14, 0),
    updatedAt: todayAt(14, 5),
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
        createdAt: todayAt(13, 0),
        updatedAt: todayAt(13, 8),
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
    getMetrics: vi.fn(async (): Promise<WorkbenchMetrics> => ({
      version: "workbench.metrics.v1",
      tasks: {
        total: 9,
        succeeded: 3,
        failed: 2,
        handoffRequired: 1,
        outcomeUncertain: 1,
      },
      actions: { total: 12, succeeded: 7, failed: 2, outcomeUncertain: 1 },
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

    expect(await screen.findByText("控制服务已连接")).toBeVisible();
    expect(screen.getByText("本机执行器在线")).toBeVisible();
    expect(screen.getByRole("heading", { name: "当前任务" })).toBeVisible();
    expect(screen.getAllByText("运行中")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "最近任务" })).toBeVisible();
    expect(screen.getByRole("button", { name: /13:00:00 的任务$/ })).toBeVisible();
    expect(screen.getByText("已成功")).toBeVisible();
    const total = screen.getByText("累计任务").closest(".ant-statistic");
    expect(total).toBeVisible();
    await waitFor(() => expect(total).toHaveTextContent("9"));
    expect(screen.getByText("当前需接管").closest(".ant-statistic")).toHaveTextContent("1");
    expect(screen.getByText("动作结果待确认").closest(".ant-statistic")).toHaveTextContent("1");
    expect(document.body).not.toHaveTextContent(/产品登录|注册账号|账号登录/);
  });

  it("names recent Tasks by when they were created, not by their identifier", async () => {
    // The workbench is the first screen of the product, and until now every row
    // of 最近任务 was labelled with the Task's UUID — 36 characters that say
    // nothing about what the Task was. The projection carries no title (see
    // `taskSnapshotSchema`: taskId/status/revision/lastEventSequence/createdAt/
    // updatedAt, and it is `.strict()`), so createdAt is the one fact in it that
    // a person can actually read.
    renderWorkbench();

    await screen.findByText("本机执行器在线");
    const rows = Array.from(
      document.querySelectorAll<HTMLElement>(".recent-task-list button"),
    );

    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row.textContent).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}:\d{2} 的任务$/);
    }
    // The fixture's two Tasks were created at 14:00 and 13:00 local time, newest
    // first — so the label is read off createdAt, not off anything else.
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("14:00:00"),
      expect.stringContaining("13:00:00"),
    ]);
    const list = document.querySelector(".recent-task-list");
    expect(list).not.toHaveTextContent(RUNNING_TASK_ID);
    expect(list).not.toHaveTextContent(COMPLETED_TASK_ID);
  });

  it("keeps the protocol counters out of the current Task body", async () => {
    // Revision and 事件水位 are the internal consistency counters of the
    // snapshot/event protocol. They belong to a diagnosis, not to the largest
    // card on the customer's first screen — but they must stay reachable,
    // because they are how an operator checks that the authoritative snapshot
    // and the event projection agree.
    const user = userEvent.setup();
    renderWorkbench();

    await screen.findByText("本机执行器在线");
    expect(screen.queryByText("Revision")).not.toBeInTheDocument();
    expect(screen.queryByText("事件水位")).not.toBeInTheDocument();
    expect(screen.queryByText(RUNNING_TASK_ID)).not.toBeInTheDocument();

    // A plain string, because the name is now exactly the label. Testing
    // Library matches a string name in full, so this would not have matched the
    // old "<折叠状态> 诊断信息" — which is why this call site used a regex until
    // T99 supplied an `aria-hidden` arrow (`components/collapse-expand-icon`).
    await user.click(screen.getByRole("button", { name: "诊断信息" }));

    // Presence, not visibility: jsdom does not run the panel's open animation,
    // so the content stays measured-but-hidden here. That the operator can
    // actually see it after the click is asserted in e2e/workbench-home.spec.ts.
    expect(await screen.findByText("Revision")).toBeInTheDocument();
    expect(screen.getByText("事件水位")).toBeInTheDocument();
    expect(screen.getByText(RUNNING_TASK_ID)).toBeInTheDocument();
  });

  it("falls back to the identifier when a Task has no readable creation time", async () => {
    // Nothing on the wire should get this far — every production path parses the
    // snapshot through Zod first. But the fallback decides what the customer's
    // first screen says if one ever does, and "NaN-NaN NaN:NaN:NaN 的任务" is
    // worse than the UUID this replaces.
    const taskSource = source();
    vi.mocked(taskSource.listTasks).mockResolvedValue({
      items: [task({ status: "succeeded", createdAt: "not a timestamp" })],
      nextCursor: null,
    });
    renderWorkbench(taskSource);

    expect(await screen.findByRole("button", { name: RUNNING_TASK_ID })).toBeVisible();
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

  it("refreshes the recent Task list for a Task the live projection does not cover", async () => {
    // The workbench follows exactly one Task. Every other row comes from the
    // list query, so without its own refresh a Task that starts running after
    // the list was read keeps rendering the status it had at that moment —
    // which is what the hidden App showed when it listed a running Task as 草稿.
    const taskSource = source();
    vi.mocked(taskSource.listTasks)
      .mockResolvedValueOnce({
        items: [
          task({ taskId: COMPLETED_TASK_ID, status: "draft", revision: 1, lastEventSequence: 0 }),
          task({ status: "draft", revision: 1, lastEventSequence: 0 }),
        ],
        nextCursor: null,
      })
      .mockResolvedValue({
        items: [
          task({ taskId: COMPLETED_TASK_ID, status: "draft", revision: 1, lastEventSequence: 0 }),
          task(),
        ],
        nextCursor: null,
      });
    renderWorkbench(taskSource);

    expect(await screen.findByText("本机执行器在线")).toBeVisible();
    expect(await screen.findByText("运行中", {}, { timeout: 4_000 })).toBeVisible();
  });

  it("confirms and sends global emergency stop through the current Task gateway", async () => {
    const workbenchGateway = gateway();
    const user = userEvent.setup();
    renderWorkbench(source(), workbenchGateway);

    await screen.findByText("本机执行器在线");
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

    await screen.findByText("本机执行器在线");
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
