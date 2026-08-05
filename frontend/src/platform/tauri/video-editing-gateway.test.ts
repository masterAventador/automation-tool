import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { VideoEditingGatewayError } from "../../features/video-editing/video-editing-gateway";
import { TauriVideoEditingGateway } from "./video-editing-gateway";

const PROJECT_ID = "f831a58a-a54c-4bd9-8f3e-0383c4df609d";
const SECOND_PROJECT_ID = "8e48954d-2df1-4168-8f33-b62c5772845c";
const TIMELINE_ID = "0a48954d-2df1-4168-8f33-b62c5772845a";
const MATERIAL_ID = "9f48954d-2df1-4168-8f33-b62c5772845b";
const JOB_ID = "3d594650-b5f4-4498-8e38-0cf85d6dfa72";

function project(projectId = PROJECT_ID) {
  return {
    projectId,
    title: "发布会剪辑",
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

function draft() {
  return {
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
            sourceMaterialId: MATERIAL_ID,
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
  };
}

function timeline(projectId = PROJECT_ID) {
  return {
    timelineId: TIMELINE_ID,
    projectId,
    revision: 1,
    ...draft(),
    createdAt: "2026-08-01T00:00:00Z",
  };
}

function job(projectId = PROJECT_ID, jobId = JOB_ID) {
  return {
    jobId,
    projectId,
    timelineId: TIMELINE_ID,
    timelineRevision: 1,
    status: "queued" as const,
    failureCode: null,
    outputArtifactId: null,
    createdAt: "2026-08-01T00:00:00Z",
    updatedAt: "2026-08-01T00:00:00Z",
  };
}

describe("Tauri video editing gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("reads every project page through the fixed command", async () => {
    invoke
      .mockResolvedValueOnce({ items: [project()], nextCursor: "page-two" })
      .mockResolvedValueOnce({ items: [project(SECOND_PROJECT_ID)], nextCursor: null });

    await expect(new TauriVideoEditingGateway().listProjects()).resolves.toEqual([
      project(),
      project(SECOND_PROJECT_ID),
    ]);
    expect(invoke.mock.calls).toEqual([
      ["list_editing_projects", { cursor: null, limit: 100 }],
      ["list_editing_projects", { cursor: "page-two", limit: 100 }],
    ]);
  });

  it("rejects repeated cursors and duplicate identities instead of looping", async () => {
    invoke
      .mockResolvedValueOnce({ items: [project()], nextCursor: "again" })
      .mockResolvedValueOnce({ items: [project(SECOND_PROJECT_ID)], nextCursor: "again" });
    await expect(new TauriVideoEditingGateway().listProjects()).rejects.toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });
    expect(invoke.mock.calls).toEqual([
      ["list_editing_projects", { cursor: null, limit: 100 }],
      ["list_editing_projects", { cursor: "again", limit: 100 }],
    ]);

    invoke.mockReset();
    invoke
      .mockResolvedValueOnce({ items: [project()], nextCursor: "next" })
      .mockResolvedValueOnce({ items: [project()], nextCursor: null });
    await expect(new TauriVideoEditingGateway().listProjects()).rejects.toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });
  });

  it("uses the other five fixed commands and validates their snapshots", async () => {
    const input = {
      title: project().title,
      output: project().output,
      captionStyle: project().captionStyle,
    };
    invoke
      .mockResolvedValueOnce(project())
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(timeline())
      .mockResolvedValueOnce({ items: [job()], nextCursor: null })
      .mockResolvedValueOnce(job());
    const gateway = new TauriVideoEditingGateway();

    await expect(gateway.createProject(input)).resolves.toEqual(project());
    await expect(gateway.getTimeline(PROJECT_ID)).resolves.toBeNull();
    await expect(gateway.saveTimeline(PROJECT_ID, draft())).resolves.toEqual(timeline());
    await expect(gateway.listEditingJobs(PROJECT_ID)).resolves.toEqual([job()]);
    await expect(gateway.submitEditingJob(PROJECT_ID)).resolves.toEqual(job());
    expect(invoke.mock.calls).toEqual([
      ["create_editing_project", { request: input }],
      ["get_editing_project_timeline", { projectId: PROJECT_ID }],
      ["save_editing_project_timeline", { projectId: PROJECT_ID, draft: draft() }],
      ["list_editing_jobs", { projectId: PROJECT_ID, cursor: null, limit: 100 }],
      ["submit_editing_job", { projectId: PROJECT_ID }],
    ]);
  });

  it("reads every job page and closes repeated job cursors", async () => {
    const secondJobId = "4d594650-b5f4-4498-8e38-0cf85d6dfa73";
    invoke
      .mockResolvedValueOnce({ items: [job()], nextCursor: "more-jobs" })
      .mockResolvedValueOnce({
        items: [job(PROJECT_ID, secondJobId)],
        nextCursor: null,
      });
    await expect(new TauriVideoEditingGateway().listEditingJobs(PROJECT_ID)).resolves.toEqual([
      job(),
      job(PROJECT_ID, secondJobId),
    ]);
    expect(invoke.mock.calls).toEqual([
      ["list_editing_jobs", { projectId: PROJECT_ID, cursor: null, limit: 100 }],
      ["list_editing_jobs", { projectId: PROJECT_ID, cursor: "more-jobs", limit: 100 }],
    ]);

    invoke.mockReset();
    invoke
      .mockResolvedValueOnce({ items: [job()], nextCursor: "again" })
      .mockResolvedValueOnce({
        items: [job(PROJECT_ID, secondJobId)],
        nextCursor: "again",
      });
    await expect(new TauriVideoEditingGateway().listEditingJobs(PROJECT_ID)).rejects.toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });
    expect(invoke).toHaveBeenCalledTimes(2);
  });

  it("rejects expanded, malformed, or cross-project native snapshots", async () => {
    invoke.mockResolvedValueOnce({
      items: [],
      nextCursor: null,
      privatePath: "/Users/private/video.mp4",
    });
    await expect(new TauriVideoEditingGateway().listProjects()).rejects.toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });

    invoke.mockResolvedValueOnce({ ...project(), privatePath: "/Users/private/video.mp4" });
    await expect(new TauriVideoEditingGateway().createProject({
      title: project().title,
      output: project().output,
      captionStyle: project().captionStyle,
    })).rejects.toMatchObject({ code: "invalid_project", retryable: false });

    invoke.mockResolvedValueOnce(timeline(SECOND_PROJECT_ID));
    await expect(new TauriVideoEditingGateway().getTimeline(PROJECT_ID)).rejects.toMatchObject({
      code: "invalid_timeline",
      retryable: false,
    });

    invoke.mockResolvedValueOnce({ items: [job(SECOND_PROJECT_ID)], nextCursor: null });
    await expect(new TauriVideoEditingGateway().listEditingJobs(PROJECT_ID)).rejects.toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });
  });

  it("binds mutation responses to the exact submitted project and timeline", async () => {
    const input = {
      title: project().title,
      output: project().output,
      captionStyle: project().captionStyle,
    };
    invoke.mockResolvedValueOnce({ ...project(), title: "另一个项目" });
    await expect(new TauriVideoEditingGateway().createProject(input)).rejects.toMatchObject({
      code: "invalid_project",
      retryable: false,
    });

    const changedTimeline = timeline();
    changedTimeline.durationMs = 4000;
    changedTimeline.tracks[0]!.clips[0]!.durationMs = 4000;
    changedTimeline.tracks[0]!.clips[0]!.sourceOutMs = 4000;
    invoke.mockResolvedValueOnce(changedTimeline);
    await expect(
      new TauriVideoEditingGateway().saveTimeline(PROJECT_ID, draft()),
    ).rejects.toMatchObject({ code: "invalid_timeline", retryable: false });

    invoke.mockResolvedValueOnce({ ...job(), status: "running" });
    await expect(
      new TauriVideoEditingGateway().submitEditingJob(PROJECT_ID),
    ).rejects.toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });
  });

  it("maps only fixed native errors and never reflects private details", async () => {
    invoke.mockRejectedValueOnce({ code: "outcome_uncertain", retryable: false });
    const uncertain = await new TauriVideoEditingGateway()
      .submitEditingJob(PROJECT_ID)
      .catch((error: unknown) => error);
    expect(uncertain).toBeInstanceOf(VideoEditingGatewayError);
    expect(uncertain).toMatchObject({ code: "outcome_uncertain", retryable: false });

    invoke.mockRejectedValueOnce({
      code: "private_native_code",
      retryable: true,
      message: "password=private-value /Users/private/video.mp4",
    });
    const opaque = await new TauriVideoEditingGateway()
      .listProjects()
      .catch((error: unknown) => error);
    expect(opaque).toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });
    expect(JSON.stringify(opaque)).not.toContain("private-value");
  });

  it("preserves retryability only for recognized native service errors", async () => {
    // 这个网关的每条命令都打到控制服务，所以已识别的控制面错误必须以
    // control_plane_unavailable 露出——2026-08-05 实测：云端旧版对
    // /api/v1/editing-projects 返回 404，前端却说「本机剪辑服务不可用」，
    // 把人指去检查一个根本没参与的进程。
    invoke.mockRejectedValueOnce({ code: "transport_unavailable", retryable: true });
    await expect(new TauriVideoEditingGateway().listProjects()).rejects.toMatchObject({
      code: "control_plane_unavailable",
      retryable: true,
    });

    // 当晚那次真实失败的形状：列表接口 404 → request_rejected。
    invoke.mockRejectedValueOnce({ code: "request_rejected", retryable: false });
    await expect(new TauriVideoEditingGateway().listProjects()).rejects.toMatchObject({
      code: "control_plane_unavailable",
      retryable: false,
    });

    invoke.mockRejectedValueOnce({
      code: "transport_unavailable",
      retryable: true,
      credential: "private-value",
    });
    await expect(new TauriVideoEditingGateway().listProjects()).rejects.toMatchObject({
      code: "editing_service_unavailable",
      retryable: false,
    });
  });

  it("rejects invalid requests before invoking native code", async () => {
    const gateway = new TauriVideoEditingGateway();
    await expect(
      gateway.createProject({
        title: "发布\u{0378}会",
        output: project().output,
        captionStyle: project().captionStyle,
      }),
    ).rejects.toMatchObject({ code: "invalid_project", retryable: false });
    await expect(gateway.getTimeline("private-invalid-project")).rejects.toMatchObject({
      code: "invalid_project",
      retryable: false,
    });
    await expect(
      gateway.saveTimeline(PROJECT_ID, { ...draft(), durationMs: 0 }),
    ).rejects.toMatchObject({ code: "invalid_timeline", retryable: false });
    expect(invoke).not.toHaveBeenCalled();
  });
});
