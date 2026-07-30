import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { TestHarnessAccountSession } from "./account-session";
import { createTestHarnessControlPlaneTransport } from "../api/control-plane/test-harness";
import {
  ControlPlaneTransportError,
  type ControlPlaneHealth,
} from "../api/control-plane/transport";
import { App } from "../app/App";
import { createTransportStartupCheck } from "../app/startup";
import { createLocalVideoEditingGateway } from "../features/video-editing/local-video-editing-gateway";
import { HARNESS_SELECTED_VIDEO, TestHarnessPublishing } from "./publishing";
import { TestHarnessTaskLifecycle } from "./task-lifecycle";
import { TestHarnessVideoStudio, type HarnessRenderEnding } from "./video-studio";
import "../styles/global.css";

const HARNESS_RUNTIME_MARKER = "automation-tool-test-harness";
const parameters = new URLSearchParams(window.location.search);
const healthMode = parameters.get("health") ?? "available";
const scenario = parameters.get("scenario");
let attempts = 0;

document.documentElement.dataset.runtime = HARNESS_RUNTIME_MARKER;

const healthy: ControlPlaneHealth = {
  status: "available",
  serviceVersion: "test-harness",
};

const transport = createTestHarnessControlPlaneTransport({
  async checkHealth() {
    attempts += 1;
    if (healthMode === "revoked") {
      throw new ControlPlaneTransportError("installation_access_denied", false);
    }
    if (healthMode === "unavailable" || (healthMode === "flaky" && attempts <= 2)) {
      throw new Error("Harness-configured unavailable state");
    }
    return healthy;
  },
});

const root = document.getElementById("root");
if (root === null) {
  throw new Error("UI Harness root is missing");
}

const taskLifecycle =
  scenario === "task-lifecycle" ? new TestHarnessTaskLifecycle() : undefined;

const publishing =
  scenario === "publishing" || scenario === "publishing-uncertain"
    ? new TestHarnessPublishing(
        scenario === "publishing-uncertain" ? "outcome_uncertain" : "published",
      )
    : undefined;
const publishingProps =
  publishing === undefined
    ? {}
    : { publishWorkspaceGateway: publishing, selectedVideo: HARNESS_SELECTED_VIDEO };
/**
 * Which scenario asks for which ending. A table rather than a chain of
 * ternaries: the third ending is where that chain stopped being readable, and
 * adding endings is the one thing this line is going to keep doing.
 */
const MOTION_RENDER_SCENARIOS: Readonly<Record<string, HarnessRenderEnding>> = {
  "motion-render-failure": "failed",
  "motion-render-success": "succeeded",
  "motion-render-cancel": "cancelled",
};
const motionRenderEnding =
  scenario === null ? undefined : MOTION_RENDER_SCENARIOS[scenario];
const videoStudioProps =
  motionRenderEnding === undefined
    ? {}
    : { materialVideoStudioGateway: new TestHarnessVideoStudio(motionRenderEnding) };
const taskLifecycleProps =
  taskLifecycle === undefined
    ? {}
    : {
        taskSource: taskLifecycle,
        taskCreationGateway: taskLifecycle,
        taskRunControlGateway: taskLifecycle,
        workbenchGateway: taskLifecycle,
      };

/**
 * The same gateway the product runs on, not a harness stand-in.
 *
 * 视频剪辑 drafts live in the browser's own storage (the cloud editing provider
 * is not connected yet), so `src/main.tsx` builds this exact object. Leaving it
 * out here made `WorkbenchShell` fall back to `shellVideoEditingGateway`, whose
 * `createProject` throws `draft_storage_unavailable` — which meant 时间轴编辑 and
 * 预览 could never render, and neither tab could be tested or even looked at.
 * Playwright gives each test a fresh context, so the store starts empty.
 */
const videoEditingGateway = createLocalVideoEditingGateway(window.sessionStorage);

/**
 * `?account=signed-in` renders the customer Demo shape: an account bar above
 * the shell. The default stays gateless so every existing spec keeps measuring
 * what it was written against.
 */
const accountProps =
  parameters.get("account") === "signed-in"
    ? { accountSessionGateway: new TestHarnessAccountSession() }
    : {};

createRoot(root).render(
  <StrictMode>
    <App
      startupCheck={createTransportStartupCheck(transport)}
      videoEditingGateway={videoEditingGateway}
      {...taskLifecycleProps}
      {...publishingProps}
      {...videoStudioProps}
      {...accountProps}
    />
  </StrictMode>,
);
