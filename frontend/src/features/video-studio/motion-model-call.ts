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
