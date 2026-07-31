// VE-08：同一剪辑工作台流程在两个模拟不同云厂商的网关上必须表现完全一致。
// 两个网关内部执行模型不同（任务推进节奏、产出 Artifact 各不相同），但都只说
// provider 中性 DTO；界面对两者不可区分，即未来新增腾讯云 Adapter 不需要改页面。
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { createLocalVideoEditingGateway } from "./local-video-editing-gateway";
import { editingJobSchema, type EditingJobSnapshot } from "./video-editing-dto";
import type { VideoEditingGateway } from "./video-editing-gateway";
import { VideoEditingWorkbench } from "./VideoEditingWorkbench";

const ARTIFACT_A = "9f48954d-2df1-4168-8f33-b62c5772845b";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}

interface SimulatedCloud {
  readonly name: string;
  readonly jobId: string;
  readonly outputArtifactId: string;
  readonly completesImmediately: boolean;
}

const FIRST_CLOUD: SimulatedCloud = {
  name: "模拟云厂商甲（提交后异步完成）",
  jobId: "1c1c1c1c-1111-4111-8111-111111111111",
  outputArtifactId: "aaaa1111-2222-4333-8444-555566667777",
  completesImmediately: false,
};

const SECOND_CLOUD: SimulatedCloud = {
  name: "模拟云厂商乙（提交后立即完成）",
  jobId: "2d2d2d2d-2222-4222-8222-222222222222",
  outputArtifactId: "bbbb1111-2222-4333-8444-555566667777",
  completesImmediately: true,
};

function simulatedCloudGateway(cloud: SimulatedCloud): VideoEditingGateway {
  const base = createLocalVideoEditingGateway(memoryStorage());
  const jobs: EditingJobSnapshot[] = [];
  return {
    listProjects: (...args) => base.listProjects(...args),
    createProject: (...args) => base.createProject(...args),
    getTimeline: (...args) => base.getTimeline(...args),
    saveTimeline: (...args) => base.saveTimeline(...args),
    async submitEditingJob(projectId) {
      const timeline = await base.getTimeline(projectId);
      if (timeline === null) {
        throw new Error("timeline missing");
      }
      const now = new Date().toISOString();
      const job = editingJobSchema.parse({
        editingJobId: cloud.jobId,
        projectId,
        timelineId: timeline.timelineId,
        timelineRevision: timeline.revision,
        status: cloud.completesImmediately ? "succeeded" : "queued",
        inputArtifactIds: [ARTIFACT_A],
        outputArtifactIds: cloud.completesImmediately ? [cloud.outputArtifactId] : [],
        failureCode: null,
        createdAt: now,
        updatedAt: now,
      });
      jobs.length = 0;
      jobs.push(job);
      return job;
    },
    async listEditingJobs() {
      // 模拟云端在两次调用之间推进任务：列表时一律已到达真实成功终态。
      return jobs.map((job) =>
        editingJobSchema.parse({
          ...job,
          status: "succeeded",
          outputArtifactIds: [cloud.outputArtifactId],
        }),
      );
    },
    async readEditingArtifact(artifactId) {
      return { artifactId, mediaType: "video/mp4", base64: "AAAA" };
    },
  };
}

async function runIdenticalUserFlow(gateway: VideoEditingGateway) {
  const user = userEvent.setup();
  const view = render(<VideoEditingWorkbench gateway={gateway} />);

  await user.type(screen.getByLabelText("剪辑项目标题"), "发布会剪辑");
  await user.type(screen.getByLabelText("输入素材引用"), ARTIFACT_A);
  await user.click(screen.getByRole("button", { name: "创建剪辑项目" }));
  expect(await screen.findByText("已创建剪辑项目：发布会剪辑")).toBeVisible();

  await user.click(screen.getByRole("tab", { name: "时间轴编辑" }));
  await user.click(screen.getByRole("button", { name: "保存时间轴" }));
  expect(await screen.findByText("已保存修订：第 1 版")).toBeVisible();

  await user.click(screen.getByRole("tab", { name: "提交与任务" }));
  await user.click(screen.getByRole("button", { name: "提交剪辑任务" }));

  expect(await screen.findByText("已完成")).toBeVisible();
  expect(screen.getByText("时间轴第 1 版")).toBeVisible();
  expect(screen.getByText(/输入素材 1 个 · 产出成片 1 个/u)).toBeVisible();

  expect(document.body).not.toHaveTextContent(
    /aliyun|阿里云|tencent|腾讯|provider|vendor|ims|ice|oss/iu,
  );

  const html = view.container.innerHTML;
  view.unmount();
  return html;
}

describe("editing provider replaceability at the page level", () => {
  it("renders the identical flow for two different simulated cloud vendors", async () => {
    const first = await runIdenticalUserFlow(simulatedCloudGateway(FIRST_CLOUD));
    const second = await runIdenticalUserFlow(simulatedCloudGateway(SECOND_CLOUD));
    expect(first).toBe(second);
  });
});
