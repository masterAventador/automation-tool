import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

import { VideoEditingGatewayError } from "../../features/video-editing/video-editing-gateway";
import type { EditingTimelineDraft } from "../../features/video-editing/video-editing-dto";
import { TauriVideoEditingGateway } from "./video-editing-gateway";

const PROJECT_ID = "00000000-0000-4000-8000-000000000101";
const TIMELINE_ID = "00000000-0000-4000-8000-000000000102";
const ARTIFACT_ID = "00000000-0000-4000-8000-000000000103";
const JOB_ID = "00000000-0000-4000-8000-000000000104";
const OUTPUT_ID = "00000000-0000-4000-8000-000000000105";

const project = {
  projectId: PROJECT_ID,
  title: "新品精剪",
  sourceArtifactIds: [ARTIFACT_ID],
  createdAt: "2026-07-31T01:02:03Z",
  updatedAt: "2026-07-31T01:02:03Z",
} as const;

const draft: EditingTimelineDraft = {
  durationMs: 3_000,
  tracks: [
    {
      trackId: "track-1",
      kind: "visual",
      clips: [
        {
          clipId: "clip-1",
          startMs: 0,
          durationMs: 3_000,
          sourceArtifactId: ARTIFACT_ID,
          text: null,
          transitionIn: null,
        },
      ],
    },
  ],
};

const timeline = {
  timelineId: TIMELINE_ID,
  projectId: PROJECT_ID,
  revision: 1,
  ...draft,
  createdAt: "2026-07-31T01:03:04Z",
} as const;

const job = {
  editingJobId: JOB_ID,
  projectId: PROJECT_ID,
  timelineId: TIMELINE_ID,
  timelineRevision: 1,
  status: "succeeded",
  inputArtifactIds: [ARTIFACT_ID],
  outputArtifactIds: [OUTPUT_ID],
  failureCode: null,
  createdAt: "2026-07-31T01:04:05Z",
  updatedAt: "2026-07-31T01:05:06Z",
} as const;

describe("Tauri video editing gateway", () => {
  beforeEach(() => invoke.mockReset());

  it("uses only the six fixed native editing commands", async () => {
    invoke
      .mockResolvedValueOnce([project])
      .mockResolvedValueOnce(project)
      .mockResolvedValueOnce(timeline)
      .mockResolvedValueOnce(timeline)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce(job)
      .mockResolvedValueOnce({
        artifactId: OUTPUT_ID,
        mediaType: "video/mp4",
        base64: "AAAA",
      });
    const gateway = new TauriVideoEditingGateway();

    await expect(gateway.listProjects()).resolves.toEqual([project]);
    await expect(
      gateway.createProject({
        title: "新品精剪",
        sourceArtifactIds: [ARTIFACT_ID],
      }),
    ).resolves.toEqual(project);
    await expect(gateway.getTimeline(PROJECT_ID)).resolves.toEqual(timeline);
    await expect(gateway.saveTimeline(PROJECT_ID, draft)).resolves.toEqual(timeline);
    await expect(gateway.listEditingJobs(PROJECT_ID)).resolves.toEqual([]);
    await expect(gateway.submitEditingJob(PROJECT_ID)).resolves.toEqual(job);
    await expect(gateway.readEditingArtifact(OUTPUT_ID)).resolves.toEqual({
      artifactId: OUTPUT_ID,
      mediaType: "video/mp4",
      base64: "AAAA",
    });

    expect(invoke.mock.calls).toEqual([
      ["list_video_editing_projects", undefined],
      [
        "create_video_editing_project",
        { request: { title: "新品精剪", sourceArtifactIds: [ARTIFACT_ID] } },
      ],
      ["get_video_editing_timeline", { projectId: PROJECT_ID }],
      ["save_video_editing_timeline", { projectId: PROJECT_ID, draft }],
      ["list_video_editing_jobs", { projectId: PROJECT_ID }],
      ["submit_video_editing_job", { projectId: PROJECT_ID }],
      ["read_video_editing_artifact", { artifactId: OUTPUT_ID }],
    ]);
  });

  it("rejects malformed native snapshots instead of trusting the IPC boundary", async () => {
    const malformed = [
      { ...project, provider: "aliyun" },
      { ...project, projectId: "not-a-uuid" },
      [{ ...timeline, revision: 0 }],
      { ...timeline, tracks: [] },
    ];
    const gateway = new TauriVideoEditingGateway();

    invoke.mockResolvedValueOnce([malformed[0]]);
    await expect(gateway.listProjects()).rejects.toMatchObject({
      code: "draft_storage_unavailable",
    });
    invoke.mockResolvedValueOnce(malformed[1]);
    await expect(
      gateway.createProject({ title: "新品精剪", sourceArtifactIds: [] }),
    ).rejects.toMatchObject({ code: "draft_storage_unavailable" });
    invoke.mockResolvedValueOnce(malformed[2]);
    await expect(gateway.listEditingJobs(PROJECT_ID)).rejects.toMatchObject({
      code: "draft_storage_unavailable",
    });
    invoke.mockResolvedValueOnce(malformed[3]);
    await expect(gateway.getTimeline(PROJECT_ID)).rejects.toMatchObject({
      code: "draft_storage_unavailable",
    });
  });

  it("maps only the closed native error vocabulary and redacts unknown failures", async () => {
    const gateway = new TauriVideoEditingGateway();

    invoke.mockRejectedValueOnce({ code: "invalid_project", retryable: false });
    await expect(gateway.getTimeline(PROJECT_ID)).rejects.toMatchObject({
      code: "invalid_project",
      retryable: false,
    });

    invoke.mockRejectedValueOnce(new Error("private path and upstream response"));
    const failure = await gateway.listProjects().catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(VideoEditingGatewayError);
    expect(failure).toMatchObject({
      code: "draft_storage_unavailable",
      retryable: false,
    });
    expect(String(failure)).not.toContain("private path");
  });
});
