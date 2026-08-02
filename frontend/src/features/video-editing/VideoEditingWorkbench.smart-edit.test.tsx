import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  smartEditFailureText,
  type SmartEditFailureCode,
  type SmartEditGateway,
  type SmartEditGenerationSnapshot,
} from "./smart-edit-gateway";
import type { VideoEditingGateway } from "./video-editing-gateway";
import { VideoEditingWorkbench } from "./VideoEditingWorkbench";

const PROJECT_ID = "0a48954d-2df1-4168-8f33-b62c5772845a";
const GENERATION_ID = "3d594650-b5f4-4498-8e38-0cf85d6dfa72";
const TIMELINE_ID = "1b70168c-90d0-4ac7-938a-51eb4754f32a";
const MATERIAL_ID = "9f48954d-2df1-4168-8f33-b62c5772845b";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function installLocalStorage(): void {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear() {
      values.clear();
    },
    getItem(key) {
      return values.get(key) ?? null;
    },
    key(index) {
      return [...values.keys()][index] ?? null;
    },
    removeItem(key) {
      values.delete(key);
    },
    setItem(key, value) {
      values.set(key, value);
    },
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storage,
  });
}

function timeline() {
  return {
    timelineId: TIMELINE_ID,
    projectId: PROJECT_ID,
    revision: 2,
    durationMs: 1_000,
    tracks: [
      {
        trackId: "visual",
        kind: "visual" as const,
        clips: [
          {
            clipId: "visual-0001",
            startMs: 0,
            durationMs: 1_000,
            sourceMaterialId: MATERIAL_ID,
            sourceInMs: 0,
            sourceOutMs: 1_000,
            text: null,
            gainDb: null,
            transitionIn: null,
            originalAudioMode: null,
          },
        ],
      },
    ],
    createdAt: "2026-08-01T00:00:00Z",
  };
}

function running(
  overrides: Partial<SmartEditGenerationSnapshot> = {},
): SmartEditGenerationSnapshot {
  return {
    generationId: GENERATION_ID,
    projectId: PROJECT_ID,
    mode: "draft",
    status: "running",
    stage: "preparing",
    progressPermille: 0,
    timeline: null,
    renderJob: null,
    failureCode: null,
    ...overrides,
  };
}

function succeeded(
  mode: "draft" | "render" = "draft",
): SmartEditGenerationSnapshot {
  return running({
    mode,
    status: "succeeded",
    stage: "completed",
    progressPermille: 1_000,
    timeline: timeline(),
    renderJob:
      mode === "render"
        ? {
            jobId: "4d594650-b5f4-4498-8e38-0cf85d6dfa73",
            projectId: PROJECT_ID,
            timelineId: TIMELINE_ID,
            timelineRevision: 2,
            status: "queued",
            failureCode: null,
            outputArtifactId: null,
            createdAt: "2026-08-01T00:00:00Z",
            updatedAt: "2026-08-01T00:00:00Z",
          }
        : null,
  });
}

function editingGateway(): VideoEditingGateway {
  return {
    async listProjects() {
      return [
        {
          projectId: PROJECT_ID,
          title: "发布会剪辑",
          output: { width: 720, height: 1280, fps: 20 },
          captionStyle: {
            fontKey: "noto-sans-cjk-sc-bold",
            fontPx: 48,
            strokePx: 3,
            lineSpacing: 1.2,
          },
          createdAt: "2026-08-01T00:00:00Z",
        },
      ];
    },
    async createProject() {
      throw new Error("unused");
    },
    async getTimeline() {
      return null;
    },
    async saveTimeline() {
      throw new Error("unused");
    },
    async listEditingJobs() {
      return [];
    },
    async submitEditingJob() {
      throw new Error("unused");
    },
  };
}

function smartEditGateway(): SmartEditGateway {
  return {
    start: vi.fn(),
    get: vi.fn(),
    cancel: vi.fn(),
    waitForTerminal: vi.fn(),
  };
}

async function openSmartEdit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "打开时间轴编辑" }));
  await user.click(screen.getByRole("tab", { name: "智能剪辑" }));
}

describe("video editing smart-edit entry", () => {
  beforeEach(() => {
    installLocalStorage();
  });

  it("offers the injected smart-edit workflow inside the existing workbench", async () => {
    render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={smartEditGateway()}
      />,
    );

    expect(await screen.findByRole("tab", { name: "智能剪辑" })).toBeVisible();
  });

  it("persists the advanced thinking preference across workbench sessions", async () => {
    const user = userEvent.setup();
    const first = render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={smartEditGateway()}
      />,
    );

    await openSmartEdit(user);
    expect(screen.getByText("高级选项")).toBeVisible();
    expect(screen.getByText("开启后预计约多花 45～64 秒。")).toBeVisible();
    expect(screen.getByRole("switch", { name: "深度思考" })).not.toBeChecked();
    await user.click(screen.getByRole("switch", { name: "深度思考" }));
    expect(screen.getByRole("switch", { name: "深度思考" })).toBeChecked();
    first.unmount();

    const second = render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={smartEditGateway()}
      />,
    );
    await openSmartEdit(user);
    expect(screen.getByRole("switch", { name: "深度思考" })).toBeChecked();
    await user.click(screen.getByRole("switch", { name: "深度思考" }));
    second.unmount();

    render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={smartEditGateway()}
      />,
    );
    await openSmartEdit(user);
    expect(screen.getByRole("switch", { name: "深度思考" })).not.toBeChecked();
  });

  it("lands a generated draft in the existing editable timeline with live progress", async () => {
    const user = userEvent.setup();
    const terminal = deferred<SmartEditGenerationSnapshot>();
    const start = vi
      .fn<SmartEditGateway["start"]>()
      .mockResolvedValue(running());
    const waitForTerminal = vi
      .fn<SmartEditGateway["waitForTerminal"]>()
      .mockImplementation(async (_generationId, options) => {
        options?.onSnapshot?.(
          running({ stage: "matching", progressPermille: 640 }),
        );
        return terminal.promise;
      });
    const gateway: SmartEditGateway = {
      ...smartEditGateway(),
      start,
      waitForTerminal,
    };
    render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={gateway}
      />,
    );

    await openSmartEdit(user);
    const prompt = screen.getByRole("textbox", { name: "一句话描述成片" });
    await user.type(prompt, "把开场剪成一条节奏明快的短片");
    await user.click(screen.getByRole("switch", { name: "深度思考" }));
    expect(screen.getByText("已开启")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "生成草稿" }));

    expect(start).toHaveBeenCalledWith({
      projectId: PROJECT_ID,
      prompt: "把开场剪成一条节奏明快的短片",
      enableThinking: true,
      mode: "draft",
    });
    expect(await screen.findByText("正在匹配画面 · 64%" as string)).toBeVisible();
    expect(screen.getByRole("button", { name: "生成草稿" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "一键直出片" })).toBeDisabled();

    await act(async () => {
      terminal.resolve(succeeded());
      await terminal.promise;
    });

    expect(await screen.findByText("当前修订：第 2 版")).toBeVisible();
    expect(screen.getByDisplayValue(MATERIAL_ID)).toBeVisible();
    expect(screen.getByRole("button", { name: "保存时间轴" })).toBeEnabled();
    expect(waitForTerminal).toHaveBeenCalledWith(
      GENERATION_ID,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("starts render once, blocks manual submission, and exposes the exact queued job", async () => {
    const user = userEvent.setup();
    const terminal = deferred<SmartEditGenerationSnapshot>();
    const start = vi
      .fn<SmartEditGateway["start"]>()
      .mockResolvedValue(running({ mode: "render" }));
    const gateway: SmartEditGateway = {
      ...smartEditGateway(),
      start,
      waitForTerminal: vi.fn(() => terminal.promise),
    };
    render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={gateway}
      />,
    );

    await openSmartEdit(user);
    await user.type(
      screen.getByRole("textbox", { name: "一句话描述成片" }),
      "直接生成发布会精华",
    );
    const renderButton = screen.getByRole("button", { name: "一键直出片" });
    await user.dblClick(renderButton);
    expect(start).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    expect(screen.getByRole("button", { name: "提交剪辑任务" })).toBeDisabled();

    await act(async () => {
      terminal.resolve(succeeded("render"));
      await terminal.promise;
    });
    expect(await screen.findByText("排队中")).toBeVisible();
    expect(screen.getByText("时间轴第 2 版")).toBeVisible();
  });

  it("requests cancellation and keeps polling until the cancelled terminal fact", async () => {
    const user = userEvent.setup();
    const terminal = deferred<SmartEditGenerationSnapshot>();
    const cancel = vi
      .fn<SmartEditGateway["cancel"]>()
      .mockResolvedValue(running({ status: "cancelling" }));
    const gateway: SmartEditGateway = {
      ...smartEditGateway(),
      start: vi.fn().mockResolvedValue(running()),
      cancel,
      waitForTerminal: vi.fn(() => terminal.promise),
    };
    render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={gateway}
      />,
    );

    await openSmartEdit(user);
    await user.type(
      screen.getByRole("textbox", { name: "一句话描述成片" }),
      "生成一个草稿",
    );
    await user.click(screen.getByRole("button", { name: "生成草稿" }));
    await user.click(await screen.findByRole("button", { name: "取消生成" }));
    expect(cancel).toHaveBeenCalledWith(GENERATION_ID);
    expect(await screen.findByText("正在取消，请稍候…")).toBeVisible();

    await act(async () => {
      terminal.resolve(running({
        status: "cancelled",
        stage: "matching",
        progressPermille: 640,
      }));
      await terminal.promise;
    });
    expect(await screen.findByText("已取消本次生成，可以修改描述后重试。")).toBeVisible();
    expect(screen.getByText("已在匹配画面阶段取消 · 64%")).toBeVisible();
    expect(screen.queryByText("正在匹配画面 · 64%")).not.toBeInTheDocument();
  });

  it("shows fixed recovery guidance without reflecting private failure details", async () => {
    const user = userEvent.setup();
    const gateway: SmartEditGateway = {
      ...smartEditGateway(),
      start: vi.fn().mockResolvedValue(running()),
      waitForTerminal: vi.fn().mockResolvedValue(
        running({
          status: "failed",
          stage: "matching",
          progressPermille: 640,
          failureCode: "material_unavailable",
        }),
      ),
    };
    render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={gateway}
      />,
    );

    await openSmartEdit(user);
    await user.type(
      screen.getByRole("textbox", { name: "一句话描述成片" }),
      "生成一个草稿",
    );
    await user.click(screen.getByRole("button", { name: "生成草稿" }));

    expect(
      await screen.findByText(
        "部分素材当前不可用，请在素材库恢复或重新导入后重试。",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "生成草稿" })).toBeEnabled();
    expect(document.body).not.toHaveTextContent(
      /[/\\]users|access.?key|transcript|provider|百炼|阿里云/iu,
    );
  });

  it("does not reflect an unexpected gateway error message", async () => {
    const user = userEvent.setup();
    const gateway: SmartEditGateway = {
      ...smartEditGateway(),
      start: vi
        .fn()
        .mockRejectedValue(
          new Error("/Users/private/secret.json model provider transcript"),
        ),
    };
    render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={gateway}
      />,
    );

    await openSmartEdit(user);
    await user.type(
      screen.getByRole("textbox", { name: "一句话描述成片" }),
      "生成一个草稿",
    );
    await user.click(screen.getByRole("button", { name: "生成草稿" }));

    expect(
      await screen.findByText(
        "智能剪辑当前不可用，请确认本机服务正在运行后重试。",
      ),
    ).toBeVisible();
    expect(document.body).not.toHaveTextContent(
      /[/\\]users|secret\.json|transcript|provider/iu,
    );
  });

  it("cancels the native generation and aborts polling when the workbench unmounts", async () => {
    const user = userEvent.setup();
    const waitStarted = deferred<void>();
    let observedSignal: AbortSignal | undefined;
    const cancel = vi
      .fn<SmartEditGateway["cancel"]>()
      .mockResolvedValue(running({ status: "cancelling" }));
    const gateway: SmartEditGateway = {
      ...smartEditGateway(),
      start: vi.fn().mockResolvedValue(running()),
      cancel,
      waitForTerminal: vi.fn(async (_generationId, options) => {
        observedSignal = options?.signal;
        waitStarted.resolve();
        return new Promise<SmartEditGenerationSnapshot>((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
        });
      }),
    };
    const view = render(
      <VideoEditingWorkbench
        gateway={editingGateway()}
        smartEditGateway={gateway}
      />,
    );

    await openSmartEdit(user);
    await user.type(
      screen.getByRole("textbox", { name: "一句话描述成片" }),
      "生成一个草稿",
    );
    await user.click(screen.getByRole("button", { name: "生成草稿" }));
    await waitStarted.promise;
    view.unmount();

    expect(observedSignal?.aborted).toBe(true);
    await waitFor(() => expect(cancel).toHaveBeenCalledWith(GENERATION_ID));
  });

  it("does not carry a completed generation status into another project", async () => {
    const user = userEvent.setup();
    const baseGateway = editingGateway();
    const projectGateway: VideoEditingGateway = {
      ...baseGateway,
      async listProjects() {
        const [first] = await baseGateway.listProjects();
        if (first === undefined) throw new Error("fixture project is missing");
        return [
          first,
          {
            ...first,
            projectId: "1a48954d-2df1-4168-8f33-b62c5772845b",
            title: "第二个剪辑项目",
          },
        ];
      },
    };
    const gateway: SmartEditGateway = {
      ...smartEditGateway(),
      start: vi.fn().mockResolvedValue(running()),
      waitForTerminal: vi.fn().mockResolvedValue(
        running({
          status: "failed",
          stage: "matching",
          progressPermille: 640,
          failureCode: "material_unavailable",
        }),
      ),
    };
    render(
      <VideoEditingWorkbench
        gateway={projectGateway}
        smartEditGateway={gateway}
      />,
    );

    const openButtons = await screen.findAllByRole("button", {
      name: "打开时间轴编辑",
    });
    await user.click(openButtons[0]!);
    await user.click(screen.getByRole("tab", { name: "智能剪辑" }));
    await user.type(
      screen.getByRole("textbox", { name: "一句话描述成片" }),
      "生成一个草稿",
    );
    await user.click(screen.getByRole("button", { name: "生成草稿" }));
    expect(
      await screen.findByText(
        "部分素材当前不可用，请在素材库恢复或重新导入后重试。",
      ),
    ).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "剪辑项目" }));
    const refreshedOpenButtons = screen.getAllByRole("button", {
      name: "打开时间轴编辑",
    });
    await user.click(refreshedOpenButtons[1]!);
    await user.click(screen.getByRole("tab", { name: "智能剪辑" }));

    expect(screen.getByText("当前项目：第二个剪辑项目")).toBeVisible();
    expect(
      screen.queryByText(
        "部分素材当前不可用，请在素材库恢复或重新导入后重试。",
      ),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("在匹配画面阶段失败 · 64%")).not.toBeInTheDocument();
  });

  it.each([
    "configuration_missing",
    "insufficient_materials",
    "source_too_short",
    "no_relevant_material",
    "material_unavailable",
    "material_snapshot_conflict",
    "timeline_revision_conflict",
    "upstream_rejected",
    "workspace_unusable",
    "commit_failed",
    "render_failed",
    "operation_unavailable",
  ] satisfies readonly SmartEditFailureCode[])(
    "maps %s to fixed safe Chinese recovery guidance",
    (code) => {
      const text = smartEditFailureText(code);
      expect(text).toMatch(/[\u3400-\u9fff]/u);
      expect(text).not.toMatch(
        /[/\\]|access.?key|prompt|transcript|model|provider|百炼|阿里云/iu,
      );
      expect(text.length).toBeGreaterThan(12);
    },
  );
});
