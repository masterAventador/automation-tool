import briefContract from "../../../../contracts/video/motion-one-sentence-brief.v1.json";

import { MOTION_DURATION_LIMITS } from "./motion-duration";

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
  durationSecondsMaximum: MOTION_DURATION_LIMITS.totalSecondsMaximum,
};

/** The shortest film worth rendering; below it there is nothing to watch. */
const DURATION_SECONDS_MINIMUM = 1;

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
