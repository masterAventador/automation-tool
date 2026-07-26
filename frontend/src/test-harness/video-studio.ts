import type {
  MaterialRenderJobSnapshot,
  MaterialVideoStudioGateway,
  MaterialVideoStudioSnapshot,
  MotionRenderJobSnapshot,
  MotionVideoBriefRequest,
  MotionVideoDraftRequest,
  RenderedVideoArtifactPayload,
} from "../features/video-studio/material-video-studio-gateway";

/**
 * A one-sentence film whose authoring succeeds and whose render then fails.
 *
 * The shell's own fallback gateway refuses the submission outright, which is
 * the timing `video-studio-one-sentence.spec.ts` already covers: the failure
 * arrives from the submit call itself. The window this harness exists for is
 * the other one — the submission succeeds, a real render job starts, and the
 * render dies afterwards, with nobody on the page to see it. Nothing in the
 * product used to look at that job unless the studio page happened to be
 * mounted, so the sidebar went on showing 正在进行中 over a film that was over.
 *
 * The render is failed on a wall clock rather than after a fixed number of
 * polls, because two different pollers read this gateway at two different
 * rates and a count would make the test depend on which of them got there
 * first. Four seconds is long enough for the operator to have clicked away and
 * short enough to keep the spec quick; the spec never asserts on the interval
 * itself, only on what the sidebar says once the render is over.
 */
const RENDER_FAILS_AFTER_MS = 4_000;
/**
 * The authoring pass is 124 seconds on the real thing (measured). It is
 * collapsed here on purpose: this scenario is about what happens *after*
 * authoring returns, and a faithful two minute wait would only make the spec
 * slow at proving something it is not testing.
 */
const AUTHORING_MS = 300;

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export class TestHarnessVideoStudio implements MaterialVideoStudioGateway {
  #job: MotionRenderJobSnapshot | null = null;
  #renderStartedAt = 0;

  async open(): Promise<MaterialVideoStudioSnapshot> {
    return { state: "opened", modelId: "qwen3.7-max-2026-06-08" };
  }

  async jobs(): Promise<readonly MaterialRenderJobSnapshot[]> {
    return [];
  }

  async cancel(): Promise<void> {
    throw new Error("Harness video studio does not cancel material jobs");
  }

  async deleteArtifact(): Promise<void> {
    throw new Error("Harness video studio does not delete material artifacts");
  }

  async submitMotionDraft(
    request: MotionVideoDraftRequest,
  ): Promise<MotionRenderJobSnapshot> {
    return this.#start(request.subject, "商务蓝");
  }

  async submitMotionBrief(
    request: MotionVideoBriefRequest,
  ): Promise<MotionRenderJobSnapshot> {
    await delay(AUTHORING_MS);
    return this.#start(request.brief, "一句话自动制作");
  }

  async motionJobs(): Promise<readonly MotionRenderJobSnapshot[]> {
    if (this.#job === null) return [];
    if (Date.now() - this.#renderStartedAt < RENDER_FAILS_AFTER_MS) return [this.#job];
    return [{ ...this.#job, status: "failed", progressPercent: 62, failureCode: "render_failed" }];
  }

  async cancelMotionRenderJob(): Promise<void> {
    throw new Error("Harness video studio does not cancel motion jobs");
  }

  async readMotionArtifact(): Promise<RenderedVideoArtifactPayload> {
    throw new Error("Harness video studio has no finished film to read");
  }

  async deleteMotionArtifact(): Promise<void> {
    throw new Error("Harness video studio does not delete motion artifacts");
  }

  async readMaterialArtifact(): Promise<RenderedVideoArtifactPayload> {
    throw new Error("Harness video studio has no finished film to read");
  }

  #start(subject: string, styleDisplayName: string): MotionRenderJobSnapshot {
    this.#renderStartedAt = Date.now();
    this.#job = {
      renderJobId: "b1f0d0c6-1d2f-4a0e-9c3a-2b6f5e7d8a90",
      revision: 1,
      status: "rendering",
      progressPercent: 20,
      subject,
      styleDisplayName,
      artifactId: null,
      artifactSizeBytes: null,
      failureCode: null,
    };
    return this.#job;
  }
}
