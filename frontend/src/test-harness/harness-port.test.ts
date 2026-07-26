import { describe, expect, it } from "vitest";

import { derivePort, harnessPort, worktreeName } from "./harness-port";

/**
 * Port 1420 was written into `playwright.config.ts` twice — as `baseURL` and
 * as `webServer.url`. One checkout, fine. This repository runs parallel lines
 * in `wt/<name>` worktrees, and on 2026-07-27 six were live at once: one line
 * hit `1420 already used` three times because another line's dev server had
 * it. The failure lands on whichever line starts second, so it reads as a
 * flaky test rather than as a fixed port.
 */
describe("harnessPort", () => {
  it("leaves the main checkout on the port everyone already knows", () => {
    expect(harnessPort("/Users/x/code/automation-tool/frontend")).toBe(1420);
  });

  it("gives a worktree its own port so two lines cannot collide", () => {
    const first = harnessPort("/Users/x/code/automation-tool/wt/alpha/frontend");
    const second = harnessPort("/Users/x/code/automation-tool/wt/beta/frontend");

    expect(first).not.toBe(1420);
    expect(first).not.toBe(second);
  });

  it("gives the same worktree the same port every run", () => {
    // A random port would make "which server is that?" unanswerable, and would
    // move the same worktree between runs.
    expect(derivePort("alpha")).toBe(derivePort("alpha"));
  });

  it("lets a human pin one explicitly", () => {
    const pinned = harnessPort("/Users/x/code/automation-tool/wt/alpha/frontend", {
      AUTOMATION_TOOL_HARNESS_PORT: "1500",
    });

    expect(pinned).toBe(1500);
  });

  it("refuses a pinned value that is not a port, rather than falling back", () => {
    // Falling back would start a server somewhere the caller did not ask for
    // and say nothing — the failure mode this whole repository keeps hitting.
    expect(() =>
      harnessPort("/Users/x/code/automation-tool/frontend", {
        AUTOMATION_TOOL_HARNESS_PORT: "not-a-port",
      }),
    ).toThrow(/not a usable port/);
  });
});

describe("worktreeName", () => {
  it("finds the name of the worktree a path is inside", () => {
    expect(worktreeName("/Users/x/code/automation-tool/wt/alpha/frontend")).toBe(
      "alpha",
    );
  });

  it("reports null for the main checkout", () => {
    expect(worktreeName("/Users/x/code/automation-tool/frontend")).toBeNull();
  });
});
