/**
 * The polling cadence for the platform-session health projection.
 *
 * Lives apart from the component so the component file only exports
 * components (react-refresh), while the cadence stays independently testable.
 */

/**
 * What the customer Demo edge permits from one address, in requests a second.
 *
 * Named here because platform sessions are the one place in the App that
 * polls, and a poller that does not know the limit will exceed it. Kept in
 * step with `limit_req_zone … rate=10r/s` in the deployed Nginx site.
 */
export const CONTROL_PLANE_REQUESTS_PER_SECOND = 10;

/**
 * The floor on how often the projection may be asked for.
 *
 * A poll costs two requests, not one: a device session is minted for every
 * control-plane call. At 500ms that is four requests a second against a limit
 * of ten — room for the rest of the App to work while this waits.
 */
export const HEALTH_PUBLICATION_MINIMUM_INTERVAL_MILLISECONDS = 500;

const HEALTH_PUBLICATION_MAXIMUM_INTERVAL_MILLISECONDS = 2_000;
const HEALTH_PUBLICATION_BUDGET_MILLISECONDS = 12_000;

/**
 * How long to wait before each re-read of the authoritative projection.
 *
 * Backs off from the floor to a ceiling, then holds, until the budget is spent.
 * It replaces a flat 50ms × 100, which asked for forty requests a second —
 * four times what the edge allows — and on 2026-08-05 spent the burst allowance
 * in about a second, so the deployment answered `429` and every platform
 * operation on that machine began reporting `operation_unavailable`. The budget
 * is longer than the 5s it replaces: the projection now gets more time, from
 * far fewer requests.
 */
export function healthPublicationDelays(): number[] {
  const delays: number[] = [];
  let spent = 0;
  let delay = HEALTH_PUBLICATION_MINIMUM_INTERVAL_MILLISECONDS;
  while (spent < HEALTH_PUBLICATION_BUDGET_MILLISECONDS) {
    delays.push(delay);
    spent += delay;
    delay = Math.min(delay * 2, HEALTH_PUBLICATION_MAXIMUM_INTERVAL_MILLISECONDS);
  }
  return delays;
}
