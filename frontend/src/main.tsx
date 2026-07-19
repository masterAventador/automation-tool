import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { createTransportStartupCheck } from "./app/startup";
import { TauriControlPlaneTransport } from "./platform/tauri/control-plane-transport";
import { TauriTaskProjectionSource } from "./platform/tauri/task-projection-source";
import { TauriTaskCreationGateway } from "./platform/tauri/task-creation-gateway";
import { TauriTaskRunControlGateway } from "./platform/tauri/task-run-control-gateway";
import { TauriWorkbenchGateway } from "./platform/tauri/workbench-gateway";
import { TauriPlatformAdapter } from "./platform/tauri/platform-adapter";
import "./styles/global.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Desktop application root is missing");
}

const startupCheck = createTransportStartupCheck(new TauriControlPlaneTransport());
const taskSource = new TauriTaskProjectionSource();
const taskCreationGateway = new TauriTaskCreationGateway();
const taskRunControlGateway = new TauriTaskRunControlGateway();
const workbenchGateway = new TauriWorkbenchGateway();
const platformAdapter = new TauriPlatformAdapter();

createRoot(root).render(
  <StrictMode>
    <App
      startupCheck={startupCheck}
      taskSource={taskSource}
      taskCreationGateway={taskCreationGateway}
      taskRunControlGateway={taskRunControlGateway}
      workbenchGateway={workbenchGateway}
      platformAdapter={platformAdapter}
    />
  </StrictMode>,
);
