/**
 * Keeps looking at a film this session started, from wherever the operator is.
 *
 * `motion-run-store.ts` moved the *result* of a run out of the page. This moves
 * the *act of going to look* out of it, which is the half T91 measured, wrote
 * down and deliberately left open: `VideoStudio.refresh()` polls every two
 * seconds, but the shell unmounts that component the moment another sidebar
 * entry is clicked. So a submission that succeeded — authoring returned, a real
 * render job started, the store holds an info message and the sidebar shows a
 * blue dot reading 视频制作正在进行中 — had nothing watching it. The render
 * could fail and the dot went on claiming progress until the operator happened
 * to open the page again. Measured: the render itself is about ten seconds for
 * a twelve second film, against two to three minutes of authoring, so this is
 * the shorter of the two windows — but it is the same lie, and this project has
 * been bitten by that exact shape repeatedly.
 *
 * What it costs, stated plainly, because a timer in the shared shell is not
 * free:
 *
 * 1. It is not resident. The effect is gated on `motionRunNeedsWatch`, a fact
 *    derived from the store, so an App that has submitted nothing never creates
 *    a timer and never calls the gateway once. It starts when a render starts
 *    and stops itself when every watched job has been seen to end — not when
 *    the operator next visits the page.
 * 2. While the studio page *is* open, its own two second poll and this one both
 *    read `motionJobs()`. That duplication is deliberate. Making this watcher
 *    stand down for a mounted page would make its correctness depend on which
 *    page is mounted, which is the property that failed in the first place. The
 *    page polls a progress bar somebody is watching; this polls a badge nobody
 *    is staring at, so it runs slower and the extra cost is a fraction of a
 *    read that was already happening.
 * 3. Cleanup is the real hazard, so there are two of them: `clearInterval` for
 *    the timer, and a `stopped` flag for a read already in flight, which can
 *    otherwise settle after teardown and write a stale answer into the store.
 */
import { useEffect, useRef } from "react";

import type {
  MaterialVideoStudioGateway,
  MotionRenderJobSnapshot,
} from "./material-video-studio-gateway";
import {
  failMotionRun,
  markMotionJobEnded,
  motionRunNeedsWatch,
  motionRunSnapshot,
  reportMotionRunTracking,
  useMotionRun,
} from "./motion-run-store";

/**
 * How often to look, in milliseconds.
 *
 * There is no correctness pressure here at all: a terminal job stays terminal,
 * so a slower tick never misses an outcome, it only learns it later. The only
 * pressure is how long the sidebar is allowed to keep saying something that has
 * stopped being true, and five seconds is well inside the time it takes a
 * person to change pages and look at anything. Measured render durations (about
 * ten seconds) informed this choice; they are deliberately not encoded as a
 * threshold anywhere, because nothing here decides anything by comparing
 * against them.
 */
const WATCH_INTERVAL_MS = 5_000;

/**
 * How many reads in a row have to fail before the App admits it cannot see.
 *
 * One failed read is a hiccup, and flipping the sidebar on every hiccup would
 * make the mark noise rather than information. Three in a row is roughly
 * fifteen seconds of genuinely not knowing, which is worth saying out loud.
 */
const LOST_AFTER_CONSECUTIVE_MISSES = 3;

const ENDED_STATUSES: ReadonlySet<MotionRenderJobSnapshot["status"]> = new Set([
  "succeeded",
  "failed",
  "cancelled",
]);

/**
 * What the sidebar says about a render that died while nobody was looking.
 *
 * One sentence, and only the part this watcher actually knows: that the film
 * did not get made. Why it failed is on the job card on the studio page, which
 * is where the operator is being sent. Repeating the reason here would both
 * duplicate that card and put this watcher in the business of classifying
 * failures, which belongs to the page and its failure codes.
 */
function renderFailedText(subject: string): string {
  return `「${subject}」这条视频没有做出来。`;
}

export function useMotionRunWatch(gateway: MaterialVideoStudioGateway): void {
  const watching = motionRunNeedsWatch(useMotionRun());
  /*
   * The gateway is read through a ref rather than named as a dependency so the
   * timer's lifetime depends on one thing only: whether there is a film to
   * watch. A caller that builds its gateway inline would otherwise restart the
   * interval on every render of the shell, and every restart fires an immediate
   * read — a render loop dressed as a poll.
   */
  const current = useRef(gateway);
  useEffect(() => {
    current.current = gateway;
  }, [gateway]);

  useEffect(() => {
    if (!watching) return;
    let stopped = false;
    let misses = 0;

    const missed = () => {
      misses += 1;
      if (misses >= LOST_AFTER_CONSECUTIVE_MISSES) reportMotionRunTracking("lost");
    };

    const look = async () => {
      let jobs: readonly MotionRenderJobSnapshot[];
      try {
        jobs = await current.current.motionJobs();
      } catch {
        if (!stopped) missed();
        return;
      }
      if (stopped) return;
      let unseen = false;
      for (const [renderJobId, own] of motionRunSnapshot().ownJobs) {
        if (own.ended) continue;
        const job = jobs.find((candidate) => candidate.renderJobId === renderJobId);
        // A job this session started that the App can no longer find is the
        // same problem as a read that threw: it is still owed an outcome and
        // there is now no way to learn one. Saying nothing would leave the
        // sidebar claiming progress, which is the whole defect.
        if (job === undefined) {
          unseen = true;
          continue;
        }
        if (!ENDED_STATUSES.has(job.status)) continue;
        if (job.status === "failed") {
          failMotionRun({ tone: "error", text: renderFailedText(job.subject) });
        }
        // Ended, not forgotten: a finished film is still owed its 去看成片
        // announcement the next time the operator opens the page.
        markMotionJobEnded(renderJobId);
      }
      if (unseen) {
        missed();
        return;
      }
      misses = 0;
      reportMotionRunTracking("ok");
    };

    void look();
    const timer = window.setInterval(() => void look(), WATCH_INTERVAL_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [watching]);
}
