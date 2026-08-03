import { describe, expect, it } from "vitest";

import { createLocalVideoEditingGateway } from "./local-video-editing-gateway";
import { VideoEditingGatewayError } from "./video-editing-gateway";

const MATERIAL_A = "9f48954d-2df1-4168-8f33-b62c5772845b";
const PROJECT_INPUT = {
  title: "发布会剪辑",
  output: { width: 720, height: 1280, fps: 20 },
  captionStyle: {
    fontKey: "noto-sans-cjk-sc-bold",
    fontPx: 48,
    strokePx: 3,
    lineSpacing: 1.2,
  },
} as const;

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}

function draft(sourceMaterialId: string = MATERIAL_A) {
  return {
    durationMs: 3_000,
    tracks: [
      {
        trackId: "track-visual",
        kind: "visual" as const,
        clips: [
          {
            clipId: "clip-1",
            startMs: 0,
            durationMs: 3_000,
            sourceMaterialId,
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
  };
}

describe("local video editing gateway", () => {
  it("creates and lists projects in the local draft store", async () => {
    const storage = memoryStorage();
    const gateway = createLocalVideoEditingGateway(storage);

    expect(await gateway.listProjects()).toEqual([]);
    const project = await gateway.createProject(PROJECT_INPUT);
    expect(project.title).toBe("发布会剪辑");
    expect(project.output).toEqual(PROJECT_INPUT.output);

    const listed = await gateway.listProjects();
    expect(listed).toHaveLength(1);
    expect(listed[0]!.projectId).toBe(project.projectId);

    const reopened = createLocalVideoEditingGateway(storage);
    expect(await reopened.listProjects()).toHaveLength(1);
  });

  it("rejects invalid project input", async () => {
    const gateway = createLocalVideoEditingGateway(memoryStorage());
    await expect(
      gateway.createProject({ ...PROJECT_INPUT, title: "   " }),
    ).rejects.toMatchObject({ code: "invalid_project" });
    await expect(
      gateway.createProject({
        ...PROJECT_INPUT,
        output: { width: 721, height: 1280, fps: 20 },
      }),
    ).rejects.toMatchObject({ code: "invalid_project" });
    await expect(
      gateway.createProject({
        ...PROJECT_INPUT,
        captionStyle: { ...PROJECT_INPUT.captionStyle, fontKey: "../font" },
      }),
    ).rejects.toMatchObject({ code: "invalid_project" });
  });

  it("saves timelines with a monotonically increasing revision", async () => {
    const storage = memoryStorage();
    const gateway = createLocalVideoEditingGateway(storage);
    const project = await gateway.createProject(PROJECT_INPUT);

    expect(await gateway.getTimeline(project.projectId)).toBeNull();

    const first = await gateway.saveTimeline(project.projectId, draft());
    expect(first.revision).toBe(1);
    expect(first.projectId).toBe(project.projectId);

    const second = await gateway.saveTimeline(project.projectId, draft());
    expect(second.revision).toBe(2);
    expect(second.timelineId).toBe(first.timelineId);

    const reopened = createLocalVideoEditingGateway(storage);
    const persisted = await reopened.getTimeline(project.projectId);
    expect(persisted?.revision).toBe(2);
  });

  it("rejects invalid drafts and unknown projects", async () => {
    const gateway = createLocalVideoEditingGateway(memoryStorage());
    const project = await gateway.createProject(PROJECT_INPUT);
    await expect(
      gateway.saveTimeline(project.projectId, {
        ...draft(),
        tracks: [],
      }),
    ).rejects.toMatchObject({ code: "invalid_timeline" });
    await expect(
      gateway.saveTimeline("0a48954d-2df1-4168-8f33-b62c5772845a", draft()),
    ).rejects.toMatchObject({ code: "invalid_project" });
  });

  it("fails closed when the local draft store is corrupted", async () => {
    const gateway = createLocalVideoEditingGateway(
      memoryStorage({ "automation-tool.video-editing.local-draft.v1": "{broken" }),
    );
    await expect(gateway.listProjects()).rejects.toBeInstanceOf(VideoEditingGatewayError);
    await expect(gateway.listProjects()).rejects.toMatchObject({
      code: "draft_storage_unavailable",
    });
  });

  it("has no editing jobs and refuses submission while the cloud service is not connected", async () => {
    const gateway = createLocalVideoEditingGateway(memoryStorage());
    const project = await gateway.createProject(PROJECT_INPUT);
    await gateway.saveTimeline(project.projectId, draft());

    expect(await gateway.listEditingJobs(project.projectId)).toEqual([]);
    await expect(gateway.submitEditingJob(project.projectId)).rejects.toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });
    expect(await gateway.listEditingJobs(project.projectId)).toEqual([]);
  });
});
