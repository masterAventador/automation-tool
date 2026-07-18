import { invoke } from "@tauri-apps/api/core";

import { createDesktopQueryClient } from "./app/query-client";
import { followTaskProjection } from "./features/task-runs/task-projection-controller";
import { TauriTaskProjectionSource } from "./platform/tauri/task-projection-source";

interface TaskProjectionPreparation {
  readonly installationId: string;
  readonly taskId: string;
}

export interface TaskProjectionAcceptanceSummary {
  readonly installationId: string;
  readonly taskId: string;
  readonly phases: readonly string[];
  readonly eventSequences: readonly number[];
  readonly finalStatus: string;
  readonly finalRevision: number;
  readonly finalLastEventSequence: number;
}

export async function runTaskProjectionAcceptance(): Promise<TaskProjectionAcceptanceSummary> {
  const preparation = await invoke<TaskProjectionPreparation>(
    "prepare_task_projection_for_acceptance",
  );
  const phases: string[] = [];
  const finalState = await followTaskProjection({
    queryClient: createDesktopQueryClient(),
    source: new TauriTaskProjectionSource(),
    taskId: preparation.taskId,
    onChange: (state) => {
      phases.push(state.phase);
    },
  });
  const snapshot = finalState.snapshot;
  if (snapshot === null || finalState.phase !== "terminal") {
    throw new Error("Task projection acceptance is unavailable");
  }
  return {
    installationId: preparation.installationId,
    taskId: preparation.taskId,
    phases,
    eventSequences: finalState.events.map((event) => event.sequence),
    finalStatus: snapshot.status,
    finalRevision: snapshot.revision,
    finalLastEventSequence: snapshot.lastEventSequence,
  };
}
