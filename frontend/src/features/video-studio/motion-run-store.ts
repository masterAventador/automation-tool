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
  /** Whether this job has been seen in a terminal state. */
  readonly ended: boolean;
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

/** Whether the runs this session is still waiting on can currently be read. */
export type MotionRunTracking = "ok" | "lost";

export interface MotionRunState {
  readonly pending: MotionRunPending | null;
  readonly message: MotionRunMessage | null;
  readonly ownJobs: ReadonlyMap<string, OwnMotionJob>;
  readonly brief: string;
  readonly selectedMethod: VideoCreationMethodId | null;
  readonly activeTab: string;
  readonly tracking: MotionRunTracking;
}

const EMPTY: MotionRunState = {
  pending: null,
  message: null,
  ownJobs: new Map(),
  brief: "",
  selectedMethod: null,
  activeTab: "new",
  tracking: "ok",
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
      ended: false,
    }),
  });
}

/**
 * This job has been seen in a terminal state, so there is nothing left to learn
 * by looking at it again.
 *
 * Distinct from `forgetMotionJob`, which drops the job entirely. A film that
 * finished is still owed an announcement and a 去看成片 button the next time the
 * operator opens the page, so it has to stay — it just stops being watched.
 */
export function markMotionJobEnded(renderJobId: string): void {
  const own = state.ownJobs.get(renderJobId);
  if (own === undefined || own.ended) return;
  commit({ ownJobs: new Map(state.ownJobs).set(renderJobId, { ...own, ended: true }) });
}

/** Whether the runs still being waited on can be read right now. */
export function reportMotionRunTracking(tracking: MotionRunTracking): void {
  if (state.tracking === tracking) return;
  commit({ tracking });
}

/**
 * Whether anything this session started is still waiting on an outcome.
 *
 * This is what decides whether the App runs a timer at all. Making it a fact
 * derived from the store, rather than something a component remembers, is what
 * keeps the watcher off entirely on a machine that has not submitted anything —
 * and what makes it stop by itself instead of running until the operator
 * happens to open the studio page again.
 */
export function motionRunNeedsWatch(current: MotionRunState): boolean {
  for (const own of current.ownJobs.values()) {
    if (!own.ended) return true;
  }
  return false;
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

/** What the sidebar has to say about this page from anywhere in the App. */
export type MotionRunAttention = "none" | "running" | "failed" | "unknown";

/**
 * Whether the sidebar should mark this page, and as what.
 *
 * "running" is a run in flight, or a result the operator has not been back to
 * see yet. Without any mark at all, leaving the page while a film is being
 * authored takes every trace of it off the screen — which is what made an
 * operator submit twice.
 *
 * "failed" is separate because one mark for both is a mark that lies. Measured
 * on 2026-07-26: a run failed at four seconds, the operator was on another
 * page, and twelve minutes later the only thing on screen about it was a dot
 * whose hover text read 视频制作正在进行中. A failure reported as progress is
 * worse than no report — the operator waits on something that is already over.
 *
 * "unknown" is the same argument applied one step further. Watching the render
 * from outside the page introduced something that can itself fail: the bridge
 * goes away, the command throws, and the App stops learning anything about a
 * film it is still waiting on. Reporting that as "running" would rebuild the
 * exact lie this state machine exists to prevent, and reporting it as "failed"
 * would assert something nothing here knows. Not being able to look is its own
 * answer. It ranks below a real failure — a known outcome always beats an
 * unknown one — and it only means anything while something is outstanding.
 */
export function motionRunAttention(current: MotionRunState): MotionRunAttention {
  if (current.message?.tone === "error") return "failed";
  if (current.tracking === "lost" && motionRunNeedsWatch(current)) return "unknown";
  if (current.pending !== null || current.message !== null) return "running";
  return "none";
}
