import type {
  TaskEvent,
  TaskProjectionRequestOptions,
  TaskProjectionSource,
  TaskSnapshot,
  TaskStatus,
} from "../api/control-plane/task-projections";
import { TaskProjectionSourceError } from "../api/control-plane/task-projections";
import type {
  DouyinSearchExposureTaskDefinition,
  TaskCreationGateway,
  TaskCreationRequestOptions,
} from "../features/task-create/task-creation-gateway";
import type {
  TaskRunControlGateway,
  TaskRunControlOperation,
  TaskRunControlReceipt,
  TaskRunControlRequestOptions,
} from "../features/task-runs/task-run-controls";
import type {
  EmergencyStopReceipt,
  WorkbenchGateway,
  WorkbenchMetrics,
  WorkbenchRequestOptions,
  WorkbenchRuntimeStatus,
} from "../features/workbench/workbench-gateway";

const STORAGE_KEY = "automation-tool-test-harness-task-lifecycle-v1";
const TERMINAL = new Set<TaskStatus>([
  "succeeded",
  "partially_succeeded",
  "failed",
  "cancelled",
  "outcome_uncertain",
]);

interface HarnessTask {
  readonly snapshot: TaskSnapshot;
  readonly events: readonly TaskEvent[];
  readonly executionAttemptId: string;
  readonly actionId: string;
}

interface HarnessState {
  readonly tasks: readonly HarnessTask[];
}

type EventListener = (event: TaskEvent) => void;

function requestCancelled(): TaskProjectionSourceError {
  return new TaskProjectionSourceError("request_cancelled", false);
}

function checkSignal(
  options:
    | TaskProjectionRequestOptions
    | TaskCreationRequestOptions
    | TaskRunControlRequestOptions
    | WorkbenchRequestOptions
    | undefined,
): void {
  if (options?.signal?.aborted === true) throw requestCancelled();
}

function event(
  taskId: string,
  executionAttemptId: string,
  actionId: string,
  sequence: number,
  taskRevision: number,
  eventType: TaskEvent["eventType"],
  taskStatus: TaskStatus,
  timestamp: string,
  progressPercent: number | null = null,
): TaskEvent {
  return {
    taskId,
    sequence,
    eventVersion: "1.0",
    eventType,
    taskRevision,
    taskStatus,
    executionAttemptId,
    actionId: eventType.startsWith("step.") ? actionId : null,
    progressPercent,
    occurredAt: timestamp,
    recordedAt: timestamp,
    message: null,
  };
}

function initialEvents(
  taskId: string,
  executionAttemptId: string,
  actionId: string,
  timestamp: string,
): readonly TaskEvent[] {
  return [
    event(taskId, executionAttemptId, actionId, 1, 2, "task.started", "running", timestamp),
    event(taskId, executionAttemptId, actionId, 2, 3, "step.started", "running", timestamp),
  ];
}

function succeededEvents(
  taskId: string,
  executionAttemptId: string,
  actionId: string,
  timestamp: string,
): readonly TaskEvent[] {
  return [
    ...initialEvents(taskId, executionAttemptId, actionId, timestamp),
    event(
      taskId,
      executionAttemptId,
      actionId,
      3,
      4,
      "step.progress",
      "running",
      timestamp,
      50,
    ),
    event(taskId, executionAttemptId, actionId, 4, 5, "step.completed", "running", timestamp),
    event(taskId, executionAttemptId, actionId, 5, 6, "task.completed", "succeeded", timestamp),
  ];
}

export class TestHarnessTaskLifecycle
  implements TaskProjectionSource, TaskCreationGateway, TaskRunControlGateway, WorkbenchGateway
{
  private readonly listeners = new Map<string, Set<EventListener>>();

  private read(): HarnessState {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (stored === null) return { tasks: [] };
    try {
      const parsed = JSON.parse(stored) as HarnessState;
      if (!Array.isArray(parsed.tasks)) return { tasks: [] };
      return parsed;
    } catch {
      return { tasks: [] };
    }
  }

  private write(state: HarnessState): void {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  private task(taskId: string): HarnessTask {
    const task = this.read().tasks.find((candidate) => candidate.snapshot.taskId === taskId);
    if (task === undefined) {
      throw new TaskProjectionSourceError("transport_unavailable", true);
    }
    return task;
  }

  private replace(task: HarnessTask): void {
    const state = this.read();
    this.write({
      tasks: state.tasks.map((candidate) =>
        candidate.snapshot.taskId === task.snapshot.taskId ? task : candidate,
      ),
    });
  }

  private publish(taskId: string, nextEvent: TaskEvent): void {
    for (const listener of this.listeners.get(taskId) ?? []) listener(nextEvent);
  }

  async createDouyinSearchExposureTask(
    _definition: DouyinSearchExposureTaskDefinition,
    _idempotencyKey: string,
    options: TaskCreationRequestOptions = {},
  ): Promise<TaskSnapshot> {
    checkSignal(options);
    const state = this.read();
    const taskId = crypto.randomUUID();
    const executionAttemptId = crypto.randomUUID();
    const actionId = crypto.randomUUID();
    const timestamp = new Date(Date.now() + state.tasks.length).toISOString();
    const receipt: TaskSnapshot = {
      taskId,
      status: "draft",
      revision: 1,
      lastEventSequence: 0,
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    const events =
      state.tasks.length === 0
        ? initialEvents(taskId, executionAttemptId, actionId, timestamp)
        : succeededEvents(taskId, executionAttemptId, actionId, timestamp);
    const finalEvent = events.at(-1)!;
    const task: HarnessTask = {
      snapshot: {
        ...receipt,
        status: finalEvent.taskStatus,
        revision: finalEvent.taskRevision,
        lastEventSequence: finalEvent.sequence,
      },
      events,
      executionAttemptId,
      actionId,
    };
    this.write({ tasks: [...state.tasks, task] });
    return receipt;
  }

  async getTask(
    taskId: string,
    options: TaskProjectionRequestOptions = {},
  ): Promise<TaskSnapshot> {
    checkSignal(options);
    return this.task(taskId).snapshot;
  }

  async listTasks({
    limit,
    signal,
  }: {
    readonly limit: number;
    readonly signal?: AbortSignal;
  }): Promise<{ items: TaskSnapshot[]; nextCursor: null }> {
    if (signal?.aborted === true) throw requestCancelled();
    return {
      items: [...this.read().tasks]
        .reverse()
        .slice(0, limit)
        .map((task) => task.snapshot),
      nextCursor: null,
    };
  }

  async streamTaskEvents(
    taskId: string,
    afterSequence: number,
    onEvent: EventListener,
    options: TaskProjectionRequestOptions = {},
  ): Promise<{ lastSequence: number; terminal: boolean }> {
    checkSignal(options);
    let lastSequence = afterSequence;
    const current = this.task(taskId);
    for (const nextEvent of current.events) {
      if (nextEvent.sequence <= lastSequence) continue;
      onEvent(nextEvent);
      lastSequence = nextEvent.sequence;
    }
    if (TERMINAL.has(current.snapshot.status)) {
      return { lastSequence, terminal: true };
    }
    return new Promise((resolve) => {
      const finish = (terminal: boolean) => {
        this.listeners.get(taskId)?.delete(listener);
        options.signal?.removeEventListener("abort", abort);
        resolve({ lastSequence, terminal });
      };
      const listener = (nextEvent: TaskEvent) => {
        if (nextEvent.sequence !== lastSequence + 1) return;
        onEvent(nextEvent);
        lastSequence = nextEvent.sequence;
        if (TERMINAL.has(nextEvent.taskStatus)) finish(true);
      };
      const abort = () => finish(false);
      const listeners = this.listeners.get(taskId) ?? new Set<EventListener>();
      listeners.add(listener);
      this.listeners.set(taskId, listeners);
      options.signal?.addEventListener("abort", abort, { once: true });
    });
  }

  private async control(
    taskId: string,
    operation: TaskRunControlOperation,
    options: TaskRunControlRequestOptions,
  ): Promise<TaskRunControlReceipt> {
    checkSignal(options);
    const current = this.task(taskId);
    const transitions = {
      pause: ["task.paused", "paused", 1],
      resume: ["task.resumed", "running", 1],
      cancel: ["task.cancelled", "cancelled", 2],
      emergency_stop: ["task.outcome_uncertain", "outcome_uncertain", 2],
    } as const;
    const [eventType, status, revisionDelta] = transitions[operation];
    const sequence = current.snapshot.lastEventSequence + 1;
    const timestamp = new Date().toISOString();
    const nextEvent = event(
      taskId,
      current.executionAttemptId,
      current.actionId,
      sequence,
      current.snapshot.revision + revisionDelta,
      eventType,
      status,
      timestamp,
    );
    const next: HarnessTask = {
      ...current,
      events: [...current.events, nextEvent],
      snapshot: {
        ...current.snapshot,
        status,
        revision: nextEvent.taskRevision,
        lastEventSequence: sequence,
        updatedAt: timestamp,
      },
    };
    this.replace(next);
    this.publish(taskId, nextEvent);
    return {
      commandId: crypto.randomUUID(),
      taskId,
      executionAttemptId: current.executionAttemptId,
      sequence,
      commandType:
        operation === "emergency_stop" ? "task.emergency_stop" : `task.${operation}`,
      status: "pending",
    };
  }

  pauseTask(
    taskId: string,
    _idempotencyKey: string,
    options: TaskRunControlRequestOptions = {},
  ): Promise<TaskRunControlReceipt> {
    return this.control(taskId, "pause", options);
  }

  resumeTask(
    taskId: string,
    _idempotencyKey: string,
    options: TaskRunControlRequestOptions = {},
  ): Promise<TaskRunControlReceipt> {
    return this.control(taskId, "resume", options);
  }

  cancelTask(
    taskId: string,
    _idempotencyKey: string,
    options: TaskRunControlRequestOptions = {},
  ): Promise<TaskRunControlReceipt> {
    return this.control(taskId, "cancel", options);
  }

  emergencyStopTask(
    taskId: string,
    _idempotencyKey: string,
    options: TaskRunControlRequestOptions = {},
  ): Promise<EmergencyStopReceipt> {
    return this.control(taskId, "emergency_stop", options).then((receipt) => ({
      ...receipt,
      commandType: "task.emergency_stop",
    }));
  }

  async getRuntimeStatus(
    options: WorkbenchRequestOptions = {},
  ): Promise<WorkbenchRuntimeStatus> {
    checkSignal(options);
    return {
      controlPlaneStatus: "ready",
      executorStatus: "online",
      executorLastHeartbeatAt: new Date().toISOString(),
    };
  }

  async getMetrics(options: WorkbenchRequestOptions = {}): Promise<WorkbenchMetrics> {
    checkSignal(options);
    const tasks = this.read().tasks.map((task) => task.snapshot);
    const succeeded = tasks.filter((task) =>
      ["succeeded", "partially_succeeded"].includes(task.status),
    ).length;
    const failed = tasks.filter((task) => task.status === "failed").length;
    const handoffRequired = tasks.filter((task) => task.status === "awaiting_human").length;
    const outcomeUncertain = tasks.filter((task) => task.status === "outcome_uncertain").length;
    return {
      version: "workbench.metrics.v1",
      tasks: { total: tasks.length, succeeded, failed, handoffRequired, outcomeUncertain },
      actions: {
        total: tasks.length,
        succeeded,
        failed,
        outcomeUncertain,
      },
    };
  }
}
