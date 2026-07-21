import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { createTransportStartupCheck } from "./app/startup";
import { TauriControlPlaneTransport } from "./platform/tauri/control-plane-transport";
import { TauriTaskProjectionSource } from "./platform/tauri/task-projection-source";
import { TauriTaskCreationGateway } from "./platform/tauri/task-creation-gateway";
import { TauriTaskRunControlGateway } from "./platform/tauri/task-run-control-gateway";
import { TauriTaskDiscoveryGateway } from "./platform/tauri/task-discovery-gateway";
import { TauriTaskTargetPreviewSource } from "./platform/tauri/task-target-preview-source";
import { TauriTaskTargetResultSource } from "./platform/tauri/task-target-result-source";
import { TauriWorkbenchGateway } from "./platform/tauri/workbench-gateway";
import { TauriPlatformAdapter } from "./platform/tauri/platform-adapter";
import { TauriPlatformSessionGateway } from "./platform/tauri/platform-session-gateway";
import "./styles/global.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Desktop application root is missing");
}

const startupCheck = createTransportStartupCheck(new TauriControlPlaneTransport());
const taskSource = new TauriTaskProjectionSource();
const taskCreationGateway = new TauriTaskCreationGateway();
const taskRunControlGateway = new TauriTaskRunControlGateway();
const taskDiscoveryGateway = new TauriTaskDiscoveryGateway();
const taskTargetPreviewSource = new TauriTaskTargetPreviewSource();
const taskTargetResultSource = new TauriTaskTargetResultSource();
const workbenchGateway = new TauriWorkbenchGateway();
const platformAdapter = new TauriPlatformAdapter();
const platformSessionGateway = new TauriPlatformSessionGateway();

createRoot(root).render(
  <StrictMode>
    <App
      startupCheck={startupCheck}
      taskSource={taskSource}
      taskCreationGateway={taskCreationGateway}
      taskRunControlGateway={taskRunControlGateway}
      taskDiscoveryGateway={taskDiscoveryGateway}
      taskTargetPreviewSource={taskTargetPreviewSource}
      taskTargetResultSource={taskTargetResultSource}
      workbenchGateway={workbenchGateway}
      platformAdapter={platformAdapter}
      platformSessionGateway={platformSessionGateway}
    />
  </StrictMode>,
);
