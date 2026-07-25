import { invoke } from "@tauri-apps/api/core";

import {
  PublishWorkspaceGatewayError,
  parsePublishWorkspaceSnapshot,
  type PublishApprovalRequest,
  type PublishRequest,
  type PublishWorkspaceGateway,
  type PublishWorkspaceSnapshot,
} from "../../features/publishing/publish-workspace-gateway";

/**
 * PB-07: the App's only route to publishing.
 *
 * Every answer is re-parsed rather than trusted: the bridge and this page ship
 * together, so a projection that does not fit the contract means one of them
 * drifted, and rendering it anyway is how a publish page starts showing things
 * the executor never said.
 */
async function projection(command: string, args: Record<string, unknown>): Promise<unknown> {
  try {
    return await invoke<unknown>(command, args);
  } catch {
    // Bridge error shapes are its own; the page only needs "this did not work".
    throw new PublishWorkspaceGatewayError("bridge_unavailable");
  }
}

export class TauriPublishWorkspaceGateway implements PublishWorkspaceGateway {
  async getWorkspace(): Promise<PublishWorkspaceSnapshot> {
    return parsePublishWorkspaceSnapshot(await projection("get_publish_workspace", {}));
  }

  async beginPublish(request: PublishRequest): Promise<PublishWorkspaceSnapshot> {
    return parsePublishWorkspaceSnapshot(
      await projection("begin_publish", {
        platform: request.platform,
        publishJobId: request.publishJobId,
        artifactPath: request.artifactPath,
        videoSummary: request.videoSummary,
        title: request.title,
        description: request.description,
      }),
    );
  }

  async approvePublish(request: PublishApprovalRequest): Promise<PublishWorkspaceSnapshot> {
    return parsePublishWorkspaceSnapshot(
      await projection("approve_publish", {
        publishJobId: request.publishJobId,
        confirmationId: request.confirmationId,
      }),
    );
  }

  async cancelPublish(): Promise<PublishWorkspaceSnapshot> {
    return parsePublishWorkspaceSnapshot(await projection("cancel_publish", {}));
  }
}
