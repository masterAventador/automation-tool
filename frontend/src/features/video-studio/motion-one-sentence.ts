import briefContract from "../../../../contracts/video/motion-one-sentence-brief.v1.json";

import { MOTION_DURATION_LIMITS, motionRenderCeilingSeconds } from "./motion-duration";

/**
 * What a one-sentence brief may contain.
 *
 * Every bound comes from a shared contract that the authoring agent also
 * reads, so the form cannot offer a choice the agent refuses. The film length
 * ceiling is deliberately taken from the storyboard duration limits rather
 * than restated here: it is the same ceiling the storyboard editor and the
 * native validator already obey, and the render sandbox is what sets it.
 */
export interface MotionBriefLimits {
  readonly maxBriefCharacters: number;
  readonly aspectRatios: readonly string[];
  readonly languages: readonly string[];
  readonly durationSecondsMaximum: number;
}

export const MOTION_BRIEF_LIMITS: MotionBriefLimits = {
  maxBriefCharacters: briefContract.maxBriefCharacters,
  aspectRatios: briefContract.aspectRatios,
  languages: briefContract.languages,
  durationSecondsMaximum: MOTION_DURATION_LIMITS.briefSecondsMaximum,
};

/**
 * How long a one-sentence film is by default.
 *
 * The operator can change it now, up to `durationSecondsMaximum`. It was fixed
 * at this value while the entry had no length control, and that turned out to
 * be why the 134 packaged parts were never used: measured 2026-07-28 against
 * the real model, a 12 second budget makes the shortest catalog part cost 37%
 * of the film and the model declined every one of them — correctly, because the
 * prompt tells it a part's length is spent from the film's budget. A 20 second
 * brief picked one to two parts.
 *
 * It is deliberately *not* parsed out of the sentence: reading "three minutes"
 * out of free text needs the model to be right about it, and a wrong answer
 * here is a film of the wrong length with no way for the user to tell why. The
 * operator says it in a control instead.
 */
export const MOTION_BRIEF_FILM_SECONDS =
  MOTION_DURATION_LIMITS.beatCountDefault * MOTION_DURATION_LIMITS.secondsPerBeatDefault;

/** The shortest film worth rendering; below it there is nothing to watch. */
export const DURATION_SECONDS_MINIMUM = 1;

/**
 * How long the authoring pass really takes, measured rather than predicted.
 *
 * Seven consecutive successful one-sentence runs on 2026-07-26: median 124
 * seconds from pressing the button to a finished film, longest 178. These are
 * the only honest numbers available — the native side's own 600 second budget
 * is a stall guard, not an expectation, and printing it would invent a ten
 * minute wait out of a two minute one.
 *
 * Spoken to the minute on purpose. "通常 2 分 4 秒" is a precision the median of
 * seven runs does not have, and a false precision is its own kind of lie.
 *
 * Here rather than in the page because two things now say it: the notice under
 * a run in flight, and the estimate under the length control. It was written
 * twice before — the second copy rounded 178 up to 180 and its own comment
 * claimed nothing else consumed it, which is exactly how the next remeasurement
 * would have corrected one of them and left the other.
 */
export const MOTION_AUTHORING_MEASURED = {
  typicalSeconds: 124,
  longestSeconds: 178,
} as const;

/** What choosing this film length will cost in waiting. */
export interface MotionBriefWaitEstimate {
  /** Roughly how many shots a film this long is cut into. */
  readonly shots: number;
  /** The longest the whole run may take: authoring, then every shot's render. */
  readonly ceilingSeconds: number;
}

/**
 * The most shots a film this long can legally be cut into.
 *
 * Both bounds come from the contract the authoring agent reads, so the number
 * shown to the operator and the storyboard the agent will accept cannot be two
 * different opinions. The first version used the *template editor's* default
 * beat length, which was never in the authoring prompt at all — 45 shots at 180
 * seconds against a hard ceiling of 24, and every phantom shot is a whole
 * `renderWallSecondsBase` of invented waiting.
 *
 * The shortest *admissible* beat is used rather than the length the agent
 * suggests, because this is a bound and the suggestion is not one: the agent
 * scales its advice with the film so a long film is cut coarsely, but a
 * storyboard that ignores the advice and stays inside the beat ceiling is still
 * accepted. Reading the suggestion here would also make a longer film report a
 * shorter wait each time the advice steps up, which is true of the advice and
 * useless to someone deciding how long a film to ask for.
 */
function shotCountCeiling(filmSeconds: number): number {
  const limits = MOTION_DURATION_LIMITS;
  return Math.max(
    1,
    Math.min(
      limits.briefBeatCountMaximum,
      Math.ceil(filmSeconds / limits.briefSecondsPerBeatMinimum),
    ),
  );
}

/**
 * How long a film of this length may keep the operator waiting.
 *
 * The number exists because the wait stopped being proportional to the film
 * when the one-sentence path became route A. Each shot is rendered on its own
 * and every render pays `renderWallSecondsBase` again — browser launch, page
 * load, warm-up — so the cost grows with how finely the film is cut, not only
 * with its length. A control that only offers the choice, without saying that,
 * invites an operator to drag it to the end and then watch a seemingly dead
 * screen for the better part of an hour.
 *
 * Composed as one render of the whole film plus the startup every extra shot
 * pays again — algebraically the same as summing per-shot ceilings, without the
 * division and multiplication that put 135 and 150 second films a whole minute
 * over their true bound.
 *
 * Reported to the whole minute. It is a ceiling, so rounding up keeps it true,
 * and «8 分 22 秒» would be read as a promise rather than as a bound.
 */
export function motionBriefWaitEstimate(filmSeconds: number): MotionBriefWaitEstimate {
  const shots = shotCountCeiling(filmSeconds);
  const seconds =
    MOTION_AUTHORING_MEASURED.longestSeconds +
    motionRenderCeilingSeconds(filmSeconds) +
    (shots - 1) * MOTION_DURATION_LIMITS.renderWallSecondsBase;
  return { shots, ceilingSeconds: Math.ceil(seconds / 60) * 60 };
}

/**
 * The plain-Chinese reason this brief cannot be submitted, or null when it can.
 *
 * Counting is done over code points rather than UTF-16 units so a sentence of
 * Chinese characters is measured the way the agent measures it.
 */
export function motionBriefProblem(
  brief: string,
  durationSeconds: number,
): string | null {
  const trimmed = brief.trim();
  if (trimmed === "") {
    return "请先用一句话描述你想要的视频内容。";
  }
  const limits = MOTION_BRIEF_LIMITS;
  if ([...trimmed].length > limits.maxBriefCharacters) {
    return `一句话描述最多 ${limits.maxBriefCharacters} 个字，请精简后再提交。`;
  }
  if (!Number.isInteger(durationSeconds) || durationSeconds < DURATION_SECONDS_MINIMUM) {
    return `片长至少 ${DURATION_SECONDS_MINIMUM} 秒，请调长片长。`;
  }
  if (durationSeconds > limits.durationSecondsMaximum) {
    return `本机最长可以制作 ${limits.durationSecondsMaximum} 秒的视频，请调短片长。`;
  }
  return null;
}
