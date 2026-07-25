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
};

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
