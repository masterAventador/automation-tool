import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { createTransportStartupCheck } from "./app/startup";
import { TauriControlPlaneTransport } from "./platform/tauri/control-plane-transport";
import { TauriTaskProjectionSource } from "./platform/tauri/task-projection-source";
import { TauriWorkbenchGateway } from "./platform/tauri/workbench-gateway";
import "./styles/global.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Desktop application root is missing");
}

const startupCheck = createTransportStartupCheck(new TauriControlPlaneTransport());
const taskSource = new TauriTaskProjectionSource();
const workbenchGateway = new TauriWorkbenchGateway();

createRoot(root).render(
  <StrictMode>
    <App
      startupCheck={startupCheck}
      taskSource={taskSource}
      workbenchGateway={workbenchGateway}
    />
  </StrictMode>,
);
