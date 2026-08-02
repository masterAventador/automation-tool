import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { createLocalVideoEditingGateway } from "./local-video-editing-gateway";
import {
  VideoEditingGatewayError,
  type VideoEditingGateway,
} from "./video-editing-gateway";
import { VideoEditingWorkbench } from "./VideoEditingWorkbench";
import type { MaterialLibraryGateway } from "./material-library-gateway";

const MATERIAL_A = "9f48954d-2df1-4168-8f33-b62c5772845b";
const MATERIAL_B = "af48954d-2df1-4168-8f33-b62c5772845c";
const PROJECT_A = "0a48954d-2df1-4168-8f33-b62c5772845a";
const PROJECT_B = "8e48954d-2df1-4168-8f33-b62c5772845c";

function projectSnapshot(projectId = PROJECT_A, title = "发布会剪辑") {
  return {
    projectId,
    title,
    output: { width: 720, height: 1280, fps: 20 },
    captionStyle: {
      fontKey: "noto-sans-cjk-sc-bold",
      fontPx: 48,
      strokePx: 3,
      lineSpacing: 1.2,
    },
    createdAt: "2026-08-01T00:00:00Z",
  };
}

function timelineSnapshot(projectId = PROJECT_A, revision = 1) {
  return {
    timelineId: "1b70168c-90d0-4ac7-938a-51eb4754f32a",
    projectId,
    revision,
    durationMs: 3000,
    tracks: [
      {
        trackId: "picture-main",
        kind: "visual" as const,
        clips: [
          {
            clipId: "opening-shot",
            startMs: 0,
            durationMs: 3000,
            sourceMaterialId: MATERIAL_A,
            sourceInMs: 0,
            sourceOutMs: 3000,
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

function jobSnapshot(projectId = PROJECT_A) {
  return {
    jobId: "3d594650-b5f4-4498-8e38-0cf85d6dfa72",
    projectId,
    timelineId: "1b70168c-90d0-4ac7-938a-51eb4754f32a",
    timelineRevision: 1,
    status: "queued" as const,
    failureCode: null,
    outputArtifactId: null,
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-01T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}

async function createProject(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("剪辑项目标题"), "发布会剪辑");
  await user.click(screen.getByRole("button", { name: "创建剪辑项目" }));
  expect(await screen.findByText("已创建剪辑项目：发布会剪辑")).toBeVisible();
}

describe("video editing workbench", () => {
  it("embeds the formal material library when its production boundary is available", async () => {
    const user = userEvent.setup();
    const listMaterials = vi
      .fn<MaterialLibraryGateway["listMaterials"]>()
      .mockResolvedValue({ items: [], nextCursor: null });
    const materialLibraryGateway: MaterialLibraryGateway = {
      listMaterials,
      async importMaterial() {
        return null;
      },
      async getMaterialStatus() {
        return "available";
      },
      async getMaterialPreviewUrl() {
        throw new Error("unused");
      },
      async updateMaterialDescription() {
        throw new Error("unused");
      },
      async deleteMaterial() {},
    };
    render(
      <VideoEditingWorkbench
        gateway={createLocalVideoEditingGateway(memoryStorage())}
        materialLibraryGateway={materialLibraryGateway}
      />,
    );

    await user.click(screen.getByRole("tab", { name: "素材库" }));
    expect(await screen.findByText("还没有本机素材")).toBeVisible();
    expect(listMaterials).toHaveBeenCalledWith(null);
  });

  it("loads projects once when the production StrictMode replays effects", async () => {
    const listProjects = vi.fn<VideoEditingGateway["listProjects"]>().mockResolvedValue([]);
    const gateway: VideoEditingGateway = {
      listProjects,
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

    render(
      <StrictMode>
        <VideoEditingWorkbench gateway={gateway} />
      </StrictMode>,
    );

    expect(await screen.findByText("还没有剪辑项目")).toBeVisible();
    expect(listProjects).toHaveBeenCalledTimes(1);
  });

  it("blocks duplicate create, save and submit actions while each request is in flight", async () => {
    const user = userEvent.setup();
    const pendingCreate = deferred<ReturnType<typeof projectSnapshot>>();
    const pendingSave = deferred<ReturnType<typeof timelineSnapshot>>();
    const pendingSubmit = deferred<ReturnType<typeof jobSnapshot>>();
    const createProject = vi
      .fn<VideoEditingGateway["createProject"]>()
      .mockImplementation(() => pendingCreate.promise);
    const saveTimeline = vi
      .fn<VideoEditingGateway["saveTimeline"]>()
      .mockImplementation(() => pendingSave.promise);
    const submitEditingJob = vi
      .fn<VideoEditingGateway["submitEditingJob"]>()
      .mockImplementation(() => pendingSubmit.promise);
    const getTimeline = vi
      .fn<VideoEditingGateway["getTimeline"]>()
      .mockResolvedValueOnce(timelineSnapshot())
      .mockResolvedValueOnce(timelineSnapshot(PROJECT_A, 2));
    const listEditingJobs = vi
      .fn<VideoEditingGateway["listEditingJobs"]>()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([jobSnapshot()]);
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [];
      },
      createProject,
      getTimeline,
      saveTimeline,
      listEditingJobs,
      submitEditingJob,
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    await screen.findByText("还没有剪辑项目");
    await user.type(screen.getByLabelText("剪辑项目标题"), "发布会剪辑");
    await user.click(screen.getByRole("button", { name: "创建剪辑项目" }));
    await user.click(screen.getByRole("button", { name: "创建剪辑项目" }));
    expect(createProject).toHaveBeenCalledTimes(1);
    await act(async () => {
      pendingCreate.resolve(projectSnapshot());
      await pendingCreate.promise;
    });

    await user.click(screen.getByRole("tab", { name: "时间轴编辑" }));
    expect(await screen.findByText("当前修订：第 1 版")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    expect(saveTimeline).toHaveBeenCalledTimes(1);
    await act(async () => {
      pendingSave.resolve(timelineSnapshot(PROJECT_A, 2));
      await pendingSave.promise;
    });
    expect(await screen.findByText("已保存修订：第 2 版")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));
    await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));
    expect(submitEditingJob).toHaveBeenCalledTimes(1);
    await act(async () => {
      pendingSubmit.resolve(jobSnapshot());
      await pendingSubmit.promise;
    });
    expect(await screen.findByText("已提交剪辑任务，正在排队。")).toBeVisible();
  });

  it("shows honest empty states without inventing projects or jobs", async () => {
    const user = userEvent.setup();
    render(
      <VideoEditingWorkbench gateway={createLocalVideoEditingGateway(memoryStorage())} />,
    );

    expect(screen.getByRole("tab", { name: "剪辑项目" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "时间轴编辑" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "预览" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "提交与任务" })).toBeVisible();
    expect(await screen.findByText("还没有剪辑项目")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "时间轴编辑" }));
    expect(screen.getByText("请先创建或选择一个剪辑项目")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "预览" }));
    expect(screen.getByText("还没有可预览内容")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    expect(screen.getByText("还没有剪辑任务")).toBeVisible();

    expect(document.body).not.toHaveTextContent(
      /moneyprinter|hyperframes|aliyun|阿里云|tencent|provider|b-roll/iu,
    );
  });

  it("creates a project and edits tracks, clips, captions, audio and transitions", async () => {
    const user = userEvent.setup();
    render(
      <VideoEditingWorkbench gateway={createLocalVideoEditingGateway(memoryStorage())} />,
    );
    await createProject(user);

    await user.click(screen.getByRole("tab", { name: "时间轴编辑" }));
    expect(screen.getByText("正在编辑：发布会剪辑")).toBeVisible();
    expect(screen.getByText("画面轨道 1")).toBeVisible();
    await user.type(screen.getByLabelText("轨道1片段1素材编号"), MATERIAL_A);

    await user.click(screen.getByRole("button", { name: "添加字幕轨道" }));
    expect(screen.getByText("字幕轨道 2")).toBeVisible();
    await user.type(screen.getByLabelText("轨道2片段1字幕文字"), "第一句字幕");

    await user.click(screen.getByRole("button", { name: "添加原声轨道" }));
    expect(screen.getByText("原声轨道 3")).toBeVisible();
    await user.type(screen.getByLabelText("轨道3片段1素材编号"), MATERIAL_A);
    expect(screen.getByLabelText("轨道3片段1原声处理")).toHaveValue("auto_duck");

    await user.click(screen.getByRole("button", { name: "在轨道1添加片段" }));
    const secondClipDuration = screen.getByLabelText("轨道1片段2时长毫秒");
    await user.clear(secondClipDuration);
    await user.type(secondClipDuration, "2000");
    await user.type(screen.getByLabelText("轨道1片段2素材编号"), MATERIAL_A);
    await user.selectOptions(screen.getByLabelText("轨道1片段2转场"), "fade");

    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    expect(await screen.findByText("已保存修订：第 1 版")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    expect(await screen.findByText("已保存修订：第 2 版")).toBeVisible();
  });

  it("creates a project with the user-selected registered caption font", async () => {
    const user = userEvent.setup();
    const create = vi
      .fn<VideoEditingGateway["createProject"]>()
      .mockImplementation(async (input) => ({
        ...projectSnapshot(),
        title: input.title,
        captionStyle: input.captionStyle,
      }));
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [];
      },
      createProject: create,
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
    render(<VideoEditingWorkbench gateway={gateway} />);

    expect(await screen.findByText("还没有剪辑项目")).toBeVisible();
    const font = screen.getByRole("combobox", { name: "字幕字体" });
    expect(font).toHaveValue("noto-sans-cjk-sc-bold");
    expect(screen.getByRole("option", { name: "遍黑体（生僻字优先）" })).toBeVisible();
    expect(
      screen.queryByRole("option", { name: /P2/u }),
    ).not.toBeInTheDocument();
    await user.selectOptions(font, "plangothic-p1-regular");
    await user.type(screen.getByLabelText("剪辑项目标题"), "生僻字字幕");
    await user.click(screen.getByRole("button", { name: "创建剪辑项目" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "生僻字字幕",
          captionStyle: expect.objectContaining({
            fontKey: "plangothic-p1-regular",
          }),
        }),
      ),
    );
  });

  it("reorders and deletes clips before saving", async () => {
    const user = userEvent.setup();
    render(
      <VideoEditingWorkbench gateway={createLocalVideoEditingGateway(memoryStorage())} />,
    );
    await createProject(user);
    await user.click(screen.getByRole("tab", { name: "时间轴编辑" }));
    await user.type(screen.getByLabelText("轨道1片段1素材编号"), MATERIAL_A);

    await user.click(screen.getByRole("button", { name: "在轨道1添加片段" }));
    const first = screen.getByLabelText("轨道1片段1时长毫秒");
    await user.clear(first);
    await user.type(first, "1000");
    const second = screen.getByLabelText("轨道1片段2时长毫秒");
    await user.clear(second);
    await user.type(second, "2000");
    await user.type(screen.getByLabelText("轨道1片段2素材编号"), MATERIAL_A);

    await user.click(screen.getByRole("button", { name: "下移轨道1片段1" }));
    expect(screen.getByLabelText("轨道1片段1时长毫秒")).toHaveValue("2000");
    expect(screen.getByLabelText("轨道1片段2时长毫秒")).toHaveValue("1000");

    await user.click(screen.getByRole("button", { name: "删除轨道1片段2" }));
    expect(screen.queryByLabelText("轨道1片段2时长毫秒")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    expect(await screen.findByText("已保存修订：第 1 版")).toBeVisible();
  });

  it("explains why an incomplete timeline cannot be saved", async () => {
    const user = userEvent.setup();
    render(
      <VideoEditingWorkbench gateway={createLocalVideoEditingGateway(memoryStorage())} />,
    );
    await createProject(user);
    await user.click(screen.getByRole("tab", { name: "时间轴编辑" }));

    await user.clear(screen.getByLabelText("轨道1片段1素材编号"));
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    expect(await screen.findByText(/时间轴还不完整/u)).toBeVisible();
    expect(screen.queryByText("已保存修订：第 1 版")).not.toBeInTheDocument();
  });

  it("previews the saved timeline structure without pretending to render video", async () => {
    const user = userEvent.setup();
    render(
      <VideoEditingWorkbench gateway={createLocalVideoEditingGateway(memoryStorage())} />,
    );
    await createProject(user);
    await user.click(screen.getByRole("tab", { name: "时间轴编辑" }));
    await user.type(screen.getByLabelText("轨道1片段1素材编号"), MATERIAL_A);
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    expect(await screen.findByText("已保存修订：第 1 版")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "预览" }));
    expect(screen.getByText("时间轴结构预览")).toBeVisible();
    expect(screen.getByText(/轨道 1（画面）/u)).toBeVisible();
    expect(
      screen.getByText("这里展示时间轴结构；本机剪辑完成后，任务列表会标记成片已入库。"),
    ).toBeVisible();
  });

  it("keeps submission honestly unavailable and never fakes job progress", async () => {
    const user = userEvent.setup();
    render(
      <VideoEditingWorkbench gateway={createLocalVideoEditingGateway(memoryStorage())} />,
    );
    await createProject(user);
    await user.click(screen.getByRole("tab", { name: "时间轴编辑" }));
    await user.type(screen.getByLabelText("轨道1片段1素材编号"), MATERIAL_A);
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    expect(await screen.findByText("已保存修订：第 1 版")).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    expect(screen.getByText("还没有剪辑任务")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));
    expect(await screen.findByText(/本机剪辑服务暂时不可用/u)).toBeVisible();
    expect(screen.getByText("还没有剪辑任务")).toBeVisible();
    expect(document.body).not.toHaveTextContent(/完成 100%|示例成片|假任务/u);
  });

  it("renders job details from the internal DTO only", async () => {
    const user = userEvent.setup();
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [
          {
            projectId: "0a48954d-2df1-4168-8f33-b62c5772845a",
            title: "发布会剪辑",
            output: { width: 720, height: 1280, fps: 20 },
            captionStyle: {
              fontKey: "noto-sans-cjk-sc-bold",
              fontPx: 48,
              strokePx: 3,
              lineSpacing: 1.2,
            },
            createdAt: "2026-07-23T00:00:00.000Z",
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
        return [
          {
            jobId: "3d594650-b5f4-4498-8e38-0cf85d6dfa72",
            projectId: "0a48954d-2df1-4168-8f33-b62c5772845a",
            timelineId: "1b70168c-90d0-4ac7-938a-51eb4754f32a",
            timelineRevision: 2,
            status: "running",
            failureCode: null,
            outputArtifactId: null,
            createdAt: "2026-07-23T00:00:00.000Z",
            updatedAt: "2026-07-23T00:00:00.000Z",
          },
        ];
      },
      async submitEditingJob() {
        throw new Error("unused");
      },
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    await user.click(await screen.findByRole("button", { name: "打开时间轴编辑" }));
    await user.click(screen.getByRole("tab", { name: "提交与任务" }));

    const job = await screen.findByText("剪辑中");
    expect(job).toBeVisible();
    expect(screen.getByText(/第 2 版/u)).toBeVisible();
    expect(document.body).not.toHaveTextContent(/provider|region|aliyun|阿里云|ims|ice/iu);
  });

  it("preserves server-issued track and clip identities when saving an edit", async () => {
    const user = userEvent.setup();
    const savedTimeline = {
      timelineId: "1b70168c-90d0-4ac7-938a-51eb4754f32a",
      projectId: "0a48954d-2df1-4168-8f33-b62c5772845a",
      revision: 1,
      durationMs: 3_000,
      tracks: [
        {
          trackId: "picture-main",
          kind: "visual" as const,
          clips: [
            {
              clipId: "opening-shot",
              startMs: 0,
              durationMs: 3_000,
              sourceMaterialId: MATERIAL_A,
              sourceInMs: 0,
              sourceOutMs: 3_000,
              text: null,
              gainDb: null,
              transitionIn: null,
              originalAudioMode: null,
            },
          ],
        },
      ],
      createdAt: "2026-07-23T00:00:00.000Z",
    };
    const saveTimeline = vi.fn<VideoEditingGateway["saveTimeline"]>();
    saveTimeline.mockResolvedValue({ ...savedTimeline, revision: 2 });
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [
          {
            projectId: savedTimeline.projectId,
            title: "发布会剪辑",
            output: { width: 720, height: 1280, fps: 20 },
            captionStyle: {
              fontKey: "noto-sans-cjk-sc-bold",
              fontPx: 48,
              strokePx: 3,
              lineSpacing: 1.2,
            },
            createdAt: savedTimeline.createdAt,
          },
        ];
      },
      async createProject() {
        throw new Error("unused");
      },
      async getTimeline() {
        return savedTimeline;
      },
      saveTimeline,
      async listEditingJobs() {
        return [];
      },
      async submitEditingJob() {
        throw new Error("unused");
      },
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    await user.click(await screen.findByRole("button", { name: "打开时间轴编辑" }));
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));

    await waitFor(() => expect(saveTimeline).toHaveBeenCalledTimes(1));
    expect(saveTimeline.mock.calls[0]?.[1].tracks[0]?.trackId).toBe("picture-main");
    expect(saveTimeline.mock.calls[0]?.[1].tracks[0]?.clips[0]?.clipId).toBe(
      "opening-shot",
    );
  });

  it("fails closed when the local draft store is corrupted", async () => {
    const gateway = createLocalVideoEditingGateway({
      getItem: () => "{broken",
      setItem: () => undefined,
    });
    render(<VideoEditingWorkbench gateway={gateway} />);

    expect(
      await screen.findByText("本机剪辑服务暂时不可用，请确认本机服务正在运行后再试。"),
    ).toBeVisible();
    expect(screen.queryByText("还没有剪辑项目")).not.toBeInTheDocument();
  });

  it("uses truthful local-service wording and exposes explicit refresh actions", async () => {
    const user = userEvent.setup();
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot()];
      },
      async createProject() {
        throw new Error("unused");
      },
      async getTimeline() {
        return timelineSnapshot();
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
    render(<VideoEditingWorkbench gateway={gateway} />);

    expect(await screen.findByRole("button", { name: "刷新项目" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "打开时间轴编辑" }));
    expect(await screen.findByRole("button", { name: "刷新时间轴" })).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    expect(screen.getByRole("button", { name: "刷新任务" })).toBeVisible();
    expect(document.body).toHaveTextContent(/本机剪辑/u);
    expect(document.body).not.toHaveTextContent(/云端|本机草稿|sessionStorage/iu);
  });

  it("shows the submitted queued job immediately and then refreshes jobs", async () => {
    const user = userEvent.setup();
    const listEditingJobs = vi
      .fn<VideoEditingGateway["listEditingJobs"]>()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([jobSnapshot()]);
    const submitEditingJob = vi
      .fn<VideoEditingGateway["submitEditingJob"]>()
      .mockResolvedValue(jobSnapshot());
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot()];
      },
      async createProject() {
        throw new Error("unused");
      },
      async getTimeline() {
        return timelineSnapshot();
      },
      async saveTimeline() {
        throw new Error("unused");
      },
      listEditingJobs,
      submitEditingJob,
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    await user.click(await screen.findByRole("button", { name: "打开时间轴编辑" }));
    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));

    expect(await screen.findByText("已提交剪辑任务，正在排队。")).toBeVisible();
    expect(screen.getByText("排队中")).toBeVisible();
    await waitFor(() => expect(listEditingJobs).toHaveBeenCalledTimes(2));
    expect(submitEditingJob).toHaveBeenCalledTimes(1);
  });

  it("does not suggest retrying when submission outcome is uncertain", async () => {
    const user = userEvent.setup();
    const listEditingJobs = vi.fn<VideoEditingGateway["listEditingJobs"]>().mockResolvedValue([]);
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot()];
      },
      async createProject() {
        throw new Error("unused");
      },
      async getTimeline() {
        return timelineSnapshot();
      },
      async saveTimeline() {
        throw new Error("unused");
      },
      listEditingJobs,
      async submitEditingJob() {
        throw new VideoEditingGatewayError("outcome_uncertain", false);
      },
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    await user.click(await screen.findByRole("button", { name: "打开时间轴编辑" }));
    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));

    const warning = await screen.findByText(/结果暂时无法确认/u);
    expect(warning).toHaveTextContent(/刷新任务列表/u);
    expect(warning).not.toHaveTextContent(/重试|重新提交/u);
    expect(screen.getByRole("button", { name: "提交剪辑任务" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    await waitFor(() => expect(listEditingJobs).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "提交剪辑任务" })).toBeEnabled();
  });

  it("requires a timeline refresh before saving again after an uncertain outcome", async () => {
    const user = userEvent.setup();
    const getTimeline = vi.fn<VideoEditingGateway["getTimeline"]>().mockResolvedValue(
      timelineSnapshot(),
    );
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot()];
      },
      async createProject() {
        throw new Error("unused");
      },
      getTimeline,
      async saveTimeline() {
        throw new VideoEditingGatewayError("outcome_uncertain", false);
      },
      async listEditingJobs() {
        return [];
      },
      async submitEditingJob() {
        throw new Error("unused");
      },
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    await user.click(await screen.findByRole("button", { name: "打开时间轴编辑" }));
    expect(await screen.findByText("当前修订：第 1 版")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));

    expect(await screen.findByText(/保存结果暂时无法确认/u)).toHaveTextContent(
      /刷新时间轴/u,
    );
    expect(screen.getByRole("button", { name: "保存时间轴" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "刷新时间轴" }));
    await waitFor(() => expect(getTimeline).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "保存时间轴" })).toBeEnabled();
  });

  it("ignores stale timeline and job responses after selecting another project", async () => {
    const user = userEvent.setup();
    const firstTimeline = deferred<ReturnType<typeof timelineSnapshot> | null>();
    const firstJobs = deferred<readonly ReturnType<typeof jobSnapshot>[]>();
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot(PROJECT_A, "项目甲"), projectSnapshot(PROJECT_B, "项目乙")];
      },
      async createProject() {
        throw new Error("unused");
      },
      getTimeline(projectId) {
        return projectId === PROJECT_A
          ? firstTimeline.promise
          : Promise.resolve(timelineSnapshot(PROJECT_B, 2));
      },
      async saveTimeline() {
        throw new Error("unused");
      },
      listEditingJobs(projectId) {
        return projectId === PROJECT_A ? firstJobs.promise : Promise.resolve([]);
      },
      async submitEditingJob() {
        throw new Error("unused");
      },
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    const openButtons = await screen.findAllByRole("button", { name: "打开时间轴编辑" });
    await user.click(openButtons[0]!);
    await user.click(screen.getByRole("tab", { name: "剪辑项目" }));
    await user.click(openButtons[1]!);
    expect(await screen.findByText("正在编辑：项目乙")).toBeVisible();
    expect(await screen.findByText("当前修订：第 2 版")).toBeVisible();

    await act(async () => {
      firstTimeline.resolve(timelineSnapshot(PROJECT_A, 1));
      firstJobs.resolve([jobSnapshot(PROJECT_A)]);
      await Promise.all([firstTimeline.promise, firstJobs.promise]);
    });
    expect(screen.getByText("当前修订：第 2 版")).toBeVisible();
    expect(screen.queryByText("当前修订：第 1 版")).not.toBeInTheDocument();
  });

  it("does not let an unfinished save from the previous project block the selected project", async () => {
    const user = userEvent.setup();
    const firstSave = deferred<ReturnType<typeof timelineSnapshot>>();
    const saveTimeline = vi
      .fn<VideoEditingGateway["saveTimeline"]>()
      .mockImplementationOnce(() => firstSave.promise)
      .mockResolvedValueOnce(timelineSnapshot(PROJECT_B, 3));
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot(PROJECT_A, "项目甲"), projectSnapshot(PROJECT_B, "项目乙")];
      },
      async createProject() {
        throw new Error("unused");
      },
      async getTimeline(projectId) {
        return timelineSnapshot(projectId, projectId === PROJECT_A ? 1 : 2);
      },
      saveTimeline,
      async listEditingJobs() {
        return [];
      },
      async submitEditingJob() {
        throw new Error("unused");
      },
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    const openButtons = await screen.findAllByRole("button", { name: "打开时间轴编辑" });
    await user.click(openButtons[0]!);
    expect(await screen.findByText("当前修订：第 1 版")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    expect(saveTimeline).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("tab", { name: "剪辑项目" }));
    await user.click(openButtons[1]!);
    expect(await screen.findByText("当前修订：第 2 版")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));

    expect(saveTimeline).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("当前修订：第 3 版")).toBeVisible();
    expect(screen.getByText(/已保存第 3 版，但暂时无法刷新时间轴/u)).toBeVisible();
    await act(async () => {
      firstSave.resolve(timelineSnapshot(PROJECT_A, 2));
      await firstSave.promise;
    });
    expect(screen.getByText("当前修订：第 3 版")).toBeVisible();
  });

  it("does not let an unfinished submission from the previous project block the selected project", async () => {
    const user = userEvent.setup();
    const firstSubmission = deferred<ReturnType<typeof jobSnapshot>>();
    const submitEditingJob = vi
      .fn<VideoEditingGateway["submitEditingJob"]>()
      .mockImplementationOnce(() => firstSubmission.promise)
      .mockResolvedValueOnce(jobSnapshot(PROJECT_B));
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot(PROJECT_A, "项目甲"), projectSnapshot(PROJECT_B, "项目乙")];
      },
      async createProject() {
        throw new Error("unused");
      },
      async getTimeline(projectId) {
        return timelineSnapshot(projectId);
      },
      async saveTimeline() {
        throw new Error("unused");
      },
      async listEditingJobs() {
        return [];
      },
      submitEditingJob,
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    const openButtons = await screen.findAllByRole("button", { name: "打开时间轴编辑" });
    await user.click(openButtons[0]!);
    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));
    expect(submitEditingJob).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("tab", { name: "剪辑项目" }));
    await user.click(openButtons[1]!);
    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));

    expect(submitEditingJob).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("已提交剪辑任务，正在排队。")).toBeVisible();
    await act(async () => {
      firstSubmission.resolve(jobSnapshot(PROJECT_A));
      await firstSubmission.promise;
    });
    expect(screen.getByText("已提交剪辑任务，正在排队。")).toBeVisible();
  });

  it("keeps edits made while an earlier timeline save is still in flight", async () => {
    const user = userEvent.setup();
    const pendingSave = deferred<ReturnType<typeof timelineSnapshot>>();
    const getTimeline = vi
      .fn<VideoEditingGateway["getTimeline"]>()
      .mockResolvedValueOnce(timelineSnapshot(PROJECT_A, 2))
      .mockResolvedValueOnce(timelineSnapshot(PROJECT_A, 3));
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot()];
      },
      async createProject() {
        throw new Error("unused");
      },
      getTimeline,
      saveTimeline() {
        return pendingSave.promise;
      },
      async listEditingJobs() {
        return [];
      },
      async submitEditingJob() {
        throw new Error("unused");
      },
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    await user.click(await screen.findByRole("button", { name: "打开时间轴编辑" }));
    expect(await screen.findByText("当前修订：第 2 版")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "保存时间轴" }));
    const materialInput = screen.getByLabelText("轨道1片段1素材编号");
    await user.clear(materialInput);
    await user.type(materialInput, MATERIAL_B);
    await act(async () => {
      pendingSave.resolve(timelineSnapshot(PROJECT_A, 3));
      await pendingSave.promise;
    });

    expect(await screen.findByText("已保存修订：第 3 版")).toBeVisible();
    expect(screen.getByLabelText("轨道1片段1素材编号")).toHaveValue(MATERIAL_B);
  });

  it("keeps the returned queued job when the immediate refresh has not observed it yet", async () => {
    const user = userEvent.setup();
    const listEditingJobs = vi.fn<VideoEditingGateway["listEditingJobs"]>().mockResolvedValue([]);
    const gateway: VideoEditingGateway = {
      async listProjects() {
        return [projectSnapshot()];
      },
      async createProject() {
        throw new Error("unused");
      },
      async getTimeline() {
        return timelineSnapshot();
      },
      async saveTimeline() {
        throw new Error("unused");
      },
      listEditingJobs,
      async submitEditingJob() {
        return jobSnapshot();
      },
    };
    render(<VideoEditingWorkbench gateway={gateway} />);

    await user.click(await screen.findByRole("button", { name: "打开时间轴编辑" }));
    await user.click(screen.getByRole("tab", { name: "提交与任务" }));
    await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));
    await waitFor(() => expect(listEditingJobs).toHaveBeenCalledTimes(2));

    expect(screen.getByText("排队中")).toBeVisible();
  });
});
