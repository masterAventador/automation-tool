import {
  SmartEditGatewayError,
  type SmartEditGateway,
  type SmartEditGenerationRequest,
  type SmartEditGenerationSnapshot,
  type SmartEditPollOptions,
} from "../features/video-editing/smart-edit-gateway";

const GENERATION_ID = "3d594650-b5f4-4498-8e38-0cf85d6dfa72";
const TIMELINE_ID = "1b70168c-90d0-4ac7-938a-51eb4754f32a";
const MATERIAL_ID = "9f48954d-2df1-4168-8f33-b62c5772845b";

function wait(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted === true) {
    return Promise.reject(new SmartEditGatewayError("polling_cancelled", false));
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(new SmartEditGatewayError("polling_cancelled", false));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, 600);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export class TestHarnessSmartEditGateway implements SmartEditGateway {
  private request: SmartEditGenerationRequest | null = null;
  private cancelling = false;

  async start(
    request: SmartEditGenerationRequest,
  ): Promise<SmartEditGenerationSnapshot> {
    this.request = request;
    this.cancelling = false;
    return this.running("preparing", 0);
  }

  async get(generationId: string): Promise<SmartEditGenerationSnapshot> {
    this.requireGeneration(generationId);
    return this.running("matching", 640);
  }

  async cancel(generationId: string): Promise<SmartEditGenerationSnapshot> {
    this.requireGeneration(generationId);
    this.cancelling = true;
    return this.running("matching", 640, "cancelling");
  }

  async waitForTerminal(
    generationId: string,
    options: SmartEditPollOptions = {},
  ): Promise<SmartEditGenerationSnapshot> {
    this.requireGeneration(generationId);
    const progress = this.running("matching", 640);
    options.onSnapshot?.(progress);
    await wait(options.signal);
    const terminal = this.cancelling ? this.cancelled() : this.succeeded();
    options.onSnapshot?.(terminal);
    return terminal;
  }

  private requireGeneration(generationId: string): SmartEditGenerationRequest {
    if (generationId !== GENERATION_ID || this.request === null) {
      throw new SmartEditGatewayError("generation_not_found", false);
    }
    return this.request;
  }

  private running(
    stage: "preparing" | "matching",
    progressPermille: number,
    status: "running" | "cancelling" = "running",
  ): SmartEditGenerationSnapshot {
    const request = this.requireRequest();
    return {
      generationId: GENERATION_ID,
      projectId: request.projectId,
      mode: request.mode,
      status,
      stage,
      progressPermille,
      timeline: null,
      renderJob: null,
      failureCode: null,
    };
  }

  private cancelled(): SmartEditGenerationSnapshot {
    const request = this.requireRequest();
    return {
      generationId: GENERATION_ID,
      projectId: request.projectId,
      mode: request.mode,
      status: "cancelled",
      stage: "matching",
      progressPermille: 640,
      timeline: null,
      renderJob: null,
      failureCode: null,
    };
  }

  private succeeded(): SmartEditGenerationSnapshot {
    const request = this.requireRequest();
    const timeline = {
      timelineId: TIMELINE_ID,
      projectId: request.projectId,
      revision: 1,
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
    return {
      generationId: GENERATION_ID,
      projectId: request.projectId,
      mode: request.mode,
      status: "succeeded",
      stage: "completed",
      progressPermille: 1_000,
      timeline,
      renderJob:
        request.mode === "render"
          ? {
              jobId: "4d594650-b5f4-4498-8e38-0cf85d6dfa73",
              projectId: request.projectId,
              timelineId: TIMELINE_ID,
              timelineRevision: 1,
              status: "queued",
              failureCode: null,
              outputArtifactId: null,
              createdAt: "2026-08-01T00:00:00Z",
              updatedAt: "2026-08-01T00:00:00Z",
            }
          : null,
      failureCode: null,
    };
  }

  private requireRequest(): SmartEditGenerationRequest {
    if (this.request === null) {
      throw new SmartEditGatewayError("generation_not_found", false);
    }
    return this.request;
  }
}
