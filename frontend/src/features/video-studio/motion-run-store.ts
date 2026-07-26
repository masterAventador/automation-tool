/**
 * The part of the video studio that has to outlive the page.
 *
 * Everything about a running film used to be React state inside `VideoStudio`,
 * and `WorkbenchShell` unmounts that component the moment the operator clicks
 * another entry in the sidebar. Measured on 2026-07-26 against the real App:
 * submit a one-sentence film, leave for 75 seconds, come back — the jobs list
 * read 还没有真实制作任务, the sentence was gone, the chosen method was gone,
 * and so was any trace that a submission had ever been made.
 *
 * The dangerous half is the third one. `submit_motion_video_brief` runs the
 * whole authoring pass inside the command, so its promise settles minutes after
 * it was sent; if it settles into a component that has been unmounted the
 * failure text is written to dead state and the operator is never told the film
 * failed. One acceptance run lost its failure reason permanently that way.
 *
 * So the run lives here, outside React, and the page renders it. Deliberately
 * not a cache and not a query: this is client-owned state about work this App
 * started, which is precisely what the project reserves a plain store for.
 * `useSyncExternalStore` rather than a library because the whole store is forty
 * lines and no dependency is installed for it.
 */
import { useSyncExternalStore } from "react";

export type VideoCreationMethodId = "material_montage_v1" | "motion_composition_v1";

/**
 * What the App knows about a render job because it is the one that started it.
 *
 * Neither fact is on `MotionRenderJobSnapshot`: it carries no start time and no
 * film length. An App restarted mid-render would otherwise count from the
 * moment it first happened to look and report "已用 3 秒" over a job four
 * minutes in — a wrong number is worse than none when the number's whole job is
 * to be evidence the run is alive. The durable fix is a `startedAt` and a
 * length on the snapshot, which is native-side work.
 */
export interface OwnMotionJob {
  readonly startedAt: number;
  readonly filmSeconds: number;
}

/** A submission that has been sent and has not come back yet. */
export interface MotionRunPending {
  readonly kind: "one_sentence" | "manual_template";
  /** What to call it on screen before the native side has named anything. */
  readonly subject: string;
  readonly startedAt: number;
}

export interface MotionRunMessage {
  readonly tone: "info" | "error";
  readonly text: string;
}

export interface MotionRunState {
  readonly pending: MotionRunPending | null;
  readonly message: MotionRunMessage | null;
  readonly ownJobs: ReadonlyMap<string, OwnMotionJob>;
  readonly brief: string;
  readonly selectedMethod: VideoCreationMethodId | null;
  readonly activeTab: string;
}

const EMPTY: MotionRunState = {
  pending: null,
  message: null,
  ownJobs: new Map(),
  brief: "",
  selectedMethod: null,
  activeTab: "new",
};

let state: MotionRunState = EMPTY;
const listeners = new Set<() => void>();

/**
 * The snapshot is replaced, never mutated, and only when something really
 * changed — `useSyncExternalStore` compares by identity and a fresh object on
 * every read is an infinite render loop.
 */
function commit(change: Partial<MotionRunState>): void {
  state = { ...state, ...change };
  for (const listener of [...listeners]) listener();
}

export function resetMotionRunStore(): void {
  state = EMPTY;
  listeners.clear();
}

export function motionRunSnapshot(): MotionRunState {
  return state;
}

export function subscribeMotionRun(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useMotionRun(): MotionRunState {
  return useSyncExternalStore(subscribeMotionRun, motionRunSnapshot);
}

/** A submission has been sent. Clears the last result: it is old news now. */
export function startMotionRun(pending: MotionRunPending): void {
  commit({ pending, message: null });
}

/** The submission came back with a job. */
export function settleMotionRun(
  renderJobId: string,
  filmSeconds: number,
  message: MotionRunMessage,
): void {
  commit({
    pending: null,
    message,
    ownJobs: new Map(state.ownJobs).set(renderJobId, {
      startedAt: Date.now(),
      filmSeconds,
    }),
  });
}

/** The submission came back a failure — possibly long after the page went away. */
export function failMotionRun(message: MotionRunMessage): void {
  commit({ pending: null, message });
}

export function dismissMotionRunMessage(): void {
  commit({ message: null });
}

export function forgetMotionJob(renderJobId: string): void {
  const ownJobs = new Map(state.ownJobs);
  if (!ownJobs.delete(renderJobId)) return;
  commit({ ownJobs });
}

export function setMotionBrief(brief: string): void {
  commit({ brief });
}

export function setMotionMethod(method: VideoCreationMethodId | null): void {
  commit({ selectedMethod: method });
}

export function setMotionActiveTab(tab: string): void {
  commit({ activeTab: tab });
}

/**
 * Whether the sidebar should mark this page as having something on it.
 *
 * A run in flight, or a result the operator has not been back to see yet.
 * Without it, leaving the page while a film is being authored takes every
 * trace of it off the screen — which is what made an operator submit twice.
 */
export function motionRunNeedsAttention(current: MotionRunState): boolean {
  return current.pending !== null || current.message !== null;
}
