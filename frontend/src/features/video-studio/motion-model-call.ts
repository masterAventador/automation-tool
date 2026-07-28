import contract from "../../../../contracts/video/motion-authoring-model-call.v1.json";

import { motionSpokenDuration } from "./motion-duration";

/**
 * How long the video-creation model may go quiet before we stop waiting.
 *
 * Taken from the same contract the authoring agent reads, so the figure the App
 * states and the budget the Executor obeys cannot become two different numbers.
 * It bounds the gap between streamed chunks rather than the whole generation:
 * "接上之后连续这么久没再返回内容" is what it means, and the sentence on the
 * card is worded that way on purpose.
 */
export const MOTION_AUTHORING_IDLE_WAIT_SECONDS: number =
  contract.streamIdleTimeoutSeconds;

/** The same wait written the way the rest of the studio says a duration. */
export const MOTION_AUTHORING_IDLE_WAIT = motionSpokenDuration(
  MOTION_AUTHORING_IDLE_WAIT_SECONDS,
);

/**
 * What the model's own reasoning costs, measured rather than guessed.
 *
 * The video-creation model reasons before it answers and that phase is most of
 * the wait. Both figures come from the shared contract so the number on screen
 * and the behaviour of the Executor cannot become two different opinions.
 */
export const MOTION_THINKING = {
  defaultEnabled: contract.thinking.defaultEnabled,
  secondsWithThinking: contract.thinking.measuredSecondsWithThinking,
  secondsWithoutThinking: contract.thinking.measuredSecondsWithoutThinking,
} as const;

/**
 * The one line under the switch, for whichever way it is set.
 *
 * The saving is computed rather than written, so the two figures and the
 * difference between them can never disagree. What is deliberately *not*
 * claimed is that turning it off costs nothing: only the time was measured, so
 * the sentence says the reasoning is what buys the extra care and leaves the
 * judgement to the operator.
 */
export function motionThinkingNotice(enabled: boolean): string {
  const saved = MOTION_THINKING.secondsWithThinking - MOTION_THINKING.secondsWithoutThinking;
  if (enabled) {
    return `开着：模型会先把思路想一遍再落笔，编排这一步实测约 ${MOTION_THINKING.secondsWithThinking} 秒。关掉能省约 ${saved} 秒，但少了这一遍推敲，分镜和文案可能没那么周全。`;
  }
  return `关掉：模型直接落笔，编排这一步实测约 ${MOTION_THINKING.secondsWithoutThinking} 秒，比开着快约 ${saved} 秒。代价是少了先想一遍的那道工序，分镜和文案可能没那么周全。`;
}
