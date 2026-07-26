/**
 * Which port this checkout's UI Harness listens on.
 *
 * Port 1420 used to be written into `playwright.config.ts` twice — once as
 * `baseURL`, once as `webServer.url`. That is fine for one checkout and wrong
 * for this repository, which runs parallel lines in `wt/<name>` worktrees: on
 * 2026-07-27 six were live at once and one line hit `1420 already used` three
 * times because another line's dev server had it. The failure lands on
 * whichever line starts second, so it reads as a flaky test rather than as a
 * fixed port.
 *
 * So the port is derived from the checkout instead of written down. The main
 * tree keeps 1420 — nothing about working there changes — and each worktree
 * gets its own. Deterministic, not random: a random port would make "which
 * server is that?" unanswerable and would move the same worktree between runs.
 */

const DEFAULT_PORT = 1420;
// Above this project's other services, below the ephemeral range the OS hands
// out, so a derived port collides with neither.
const DERIVED_FIRST = 14200;
const DERIVED_COUNT = 400;

/** The worktree a path sits in, or null when it is the main checkout. */
export function worktreeName(directory: string): string | null {
  const match = /\/wt\/([^/]+)(?:\/|$)/.exec(directory);
  return match?.[1] ?? null;
}

/** Same name, same port, every run and every machine. */
export function derivePort(name: string): number {
  let hash = 0;
  for (const character of name) {
    hash = (hash * 31 + (character.codePointAt(0) ?? 0)) % DERIVED_COUNT;
  }
  return DERIVED_FIRST + hash;
}

export function harnessPort(
  directory: string,
  environment: Record<string, string | undefined> = {},
): number {
  const requested = environment.AUTOMATION_TOOL_HARNESS_PORT;
  if (requested !== undefined) {
    const parsed = Number.parseInt(requested, 10);
    if (!Number.isInteger(parsed) || parsed <= 0 || parsed >= 65536) {
      // Falling back would start a server somewhere the caller did not ask for
      // and say nothing about it.
      throw new Error(
        `AUTOMATION_TOOL_HARNESS_PORT is not a usable port: ${requested}`,
      );
    }
    return parsed;
  }
  const name = worktreeName(directory);
  return name === null ? DEFAULT_PORT : derivePort(name);
}
