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
 * What turning the model's own reasoning off saves, measured rather than guessed.
 *
 * A *saving*, never an absolute wait, and that is a correction rather than a
 * preference. The measurement is in **model seconds** — one round trip — while
 * everything else this page says about authoring is **end-to-end wall clock**
 * (`MOTION_AUTHORING_MEASURED`, 136 to 178 seconds). `demo-sprint-roadmap.md`
 * records the rule that came out of T92: the two are different units, and
 * swapping one for the other is worse than leaving the old number alone. The
 * first version of this printed «编排这一步实测约 42 秒» directly above a line
 * reading «编排加渲染最长约 …», which is both units on one screen.
 *
 * The saving is the one figure that carries across, because turning reasoning
 * off removes the same phase from both.
 */
export const MOTION_THINKING = {
  defaultEnabled: contract.thinking.defaultEnabled,
  savedSecondsTypical: contract.thinking.savedSecondsTypical,
  savedSecondsLeast: contract.thinking.savedSecondsLeast,
  savedSecondsMost: contract.thinking.savedSecondsMost,
} as const;

/**
 * The one line under the switch, for whichever way it is set.
 *
 * Every figure comes from the contract, so the sentence and the Executor cannot
 * become two opinions. What is deliberately *not* claimed is that turning it
 * off is worse: only the time was measured, so the sentence says so outright
 * rather than implying a quality cost nobody has evidence for. Both directions
 * say the same thing, because a switch whose two labels disagree about the
 * trade is a recommendation wearing a switch's clothes.
 */
export function motionThinkingNotice(enabled: boolean): string {
  const { savedSecondsTypical, savedSecondsLeast, savedSecondsMost } = MOTION_THINKING;
  if (enabled) {
    return `开着：模型会先把思路想一遍再落笔。关掉大约能省 ${savedSecondsTypical} 秒（实测 3 次，${savedSecondsLeast}~${savedSecondsMost} 秒）。少了这一遍推敲成片会不会变差，我们还没有量过。`;
  }
  return `关掉：模型直接落笔，比开着大约快 ${savedSecondsTypical} 秒。省下的是先想一遍那道工序；它对成片有多大影响，我们还没有量过。`;
}
