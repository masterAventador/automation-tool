import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createTestHarnessControlPlaneTransport } from "../api/control-plane/test-harness";
import {
  ControlPlaneTransportError,
  type ControlPlaneHealth,
} from "../api/control-plane/transport";
import { App } from "../app/App";
import { createTransportStartupCheck } from "../app/startup";
import { TestHarnessTaskLifecycle } from "./task-lifecycle";
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
const taskLifecycleProps =
  taskLifecycle === undefined
    ? {}
    : {
        taskSource: taskLifecycle,
        taskCreationGateway: taskLifecycle,
        taskRunControlGateway: taskLifecycle,
        workbenchGateway: taskLifecycle,
      };

createRoot(root).render(
  <StrictMode>
    <App
      startupCheck={createTransportStartupCheck(transport)}
      {...taskLifecycleProps}
    />
  </StrictMode>,
);
