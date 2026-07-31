import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { createLocalVideoEditingGateway } from "./local-video-editing-gateway";
import type { VideoEditingGateway } from "./video-editing-gateway";
import { VideoEditingWorkbench } from "./VideoEditingWorkbench";

const MATERIAL_A = "9f48954d-2df1-4168-8f33-b62c5772845b";

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
      screen.getByText("视频画面预览将在云端剪辑服务接入后提供。"),
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
    expect(await screen.findByText(/云端剪辑功能尚未开通/u)).toBeVisible();
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
      await screen.findByText("本机剪辑草稿暂时无法读取，请稍后重试。"),
    ).toBeVisible();
    expect(screen.queryByText("还没有剪辑项目")).not.toBeInTheDocument();
  });
});
