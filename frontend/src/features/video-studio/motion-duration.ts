import contract from "../../../../contracts/video/motion-storyboard-duration.v1.json";

/**
 * How long a brand-motion film may be. Every bound comes from the shared
 * contract that `motion_video_studio.rs` also reads, so the editor and the
 * native validator can never drift apart.
 */
export interface MotionDurationLimits {
  readonly framesPerSecond: number;
  readonly beatCountMinimum: number;
  readonly beatCountMaximum: number;
  readonly beatCountDefault: number;
  readonly secondsPerBeatMinimum: number;
  readonly secondsPerBeatMaximum: number;
  readonly secondsPerBeatDefault: number;
  readonly totalSecondsMaximum: number;
  /**
   * The longest film the one-sentence entry lets the operator ask for.
   *
   * Larger than `totalSecondsMaximum`, which is the sandbox's single-capture
   * limit and still binds the fixed-template path. A one-sentence film is one
   * render per shot and joined afterwards, so its ceiling is a product decision
   * rather than a sandbox one.
   */
  readonly briefSecondsMaximum: number;
  /** Fixed startup cost of a render: browser launch, page load, warm-up. */
  readonly renderWallSecondsBase: number;
  /** Per-frame cost of a render: seek, composite, capture. */
  readonly renderWallMillisPerFrame: number;
}

export const MOTION_DURATION_LIMITS: MotionDurationLimits = {
  framesPerSecond: contract.framesPerSecond,
  beatCountMinimum: contract.beatCountMinimum,
  beatCountMaximum: contract.beatCountMaximum,
  beatCountDefault: contract.beatCountDefault,
  secondsPerBeatMinimum: contract.secondsPerBeatMinimum,
  secondsPerBeatMaximum: contract.secondsPerBeatMaximum,
  secondsPerBeatDefault: contract.secondsPerBeatDefault,
  totalSecondsMaximum: contract.totalSecondsMaximum,
  briefSecondsMaximum: contract.briefSecondsMaximum,
  renderWallSecondsBase: contract.renderWallSecondsBase,
  renderWallMillisPerFrame: contract.renderWallMillisPerFrame,
};

/**
 * The longest the local render of a film this length may run before the
 * sandbox stops it.
 *
 * A ceiling, not an average: the contract's `renderWall` rationale sizes these
 * two numbers so that the longest legal film asks for 270 seconds and stays
 * inside the sandbox's 300 second stall guard. A real twelve second render
 * finishes well under its ceiling. That is exactly why the number is worth
 * showing — it is the point at which the software itself gives up, so anything
 * before it is not yet a reason to think the job is stuck.
 */
export function motionRenderCeilingSeconds(filmSeconds: number): number {
  const limits = MOTION_DURATION_LIMITS;
  const frames = filmSeconds * limits.framesPerSecond;
  return limits.renderWallSecondsBase + (frames * limits.renderWallMillisPerFrame) / 1000;
}

/** A number of seconds, written the way it is said out loud. */
export function motionSpokenDuration(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  if (minutes === 0) return `${rest} 秒`;
  if (rest === 0) return `${minutes} 分`;
  return `${minutes} 分 ${rest} 秒`;
}

/**
 * The plain-Chinese reason this storyboard cannot be rendered, or null when it
 * can. Both factors are checked on their own and so is their product: a legal
 * beat count and a legal beat length can still ask for a film longer than the
 * local render sandbox is able to capture.
 */
export function motionDurationProblem(
  beatCount: number,
  secondsPerBeat: number,
): string | null {
  const limits = MOTION_DURATION_LIMITS;
  if (
    !Number.isInteger(beatCount) ||
    beatCount < limits.beatCountMinimum ||
    beatCount > limits.beatCountMaximum
  ) {
    return `段数需要是 ${limits.beatCountMinimum} 到 ${limits.beatCountMaximum} 之间的整数。`;
  }
  if (
    !Number.isInteger(secondsPerBeat) ||
    secondsPerBeat < limits.secondsPerBeatMinimum ||
    secondsPerBeat > limits.secondsPerBeatMaximum
  ) {
    return `每段时长需要是 ${limits.secondsPerBeatMinimum} 到 ${limits.secondsPerBeatMaximum} 秒之间的整数。`;
  }
  const total = beatCount * secondsPerBeat;
  if (total > limits.totalSecondsMaximum) {
    return `成片总长最多 ${limits.totalSecondsMaximum} 秒，当前 ${beatCount} 段 × ${secondsPerBeat} 秒 = ${total} 秒，请减少段数或缩短每段时长。`;
  }
  return null;
}

/** The one line that tells the user what the current settings will produce. */
export function motionStoryboardSummary(
  beatCount: number,
  secondsPerBeat: number,
): string {
  return `共 ${beatCount} 段 · 每段 ${secondsPerBeat} 秒 · 成片约 ${beatCount * secondsPerBeat} 秒`;
}

/**
 * Grows or shrinks a per-beat list to `count` entries without disturbing the
 * entries the user already wrote. Returns the original list untouched when the
 * length already matches, so React state does not churn on every keystroke.
 */
export function resizeMotionBeats<T>(
  beats: readonly T[],
  count: number,
  create: (index: number) => T,
): readonly T[] {
  if (count === beats.length) return beats;
  if (count < beats.length) return beats.slice(0, count);
  return [
    ...beats,
    ...Array.from({ length: count - beats.length }, (_unused, offset) =>
      create(beats.length + offset),
    ),
  ];
}
