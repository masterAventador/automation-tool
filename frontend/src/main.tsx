import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { createDesktopStartupCheck } from "./app/startup";
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
import { TauriStartupEnvironmentGateway } from "./platform/tauri/startup-environment-gateway";
import { TauriAppUpdateGateway } from "./platform/tauri/app-update-gateway";
import { UnifiedLoginGate } from "./features/account-session/UnifiedLoginGate";
import { createUnifiedLoginClient } from "./platform/tauri/unified-login-client";
import { TauriModelServiceGateway } from "./platform/tauri/model-service-gateway";
import { TauriBilibiliServiceGateway } from "./platform/tauri/bilibili-service-gateway";
import { TauriMaterialVideoStudioGateway } from "./platform/tauri/material-video-studio-gateway";
import { TauriPublishWorkspaceGateway } from "./platform/tauri/publish-workspace-gateway";
import { TauriVideoEditingGateway } from "./platform/tauri/video-editing-gateway";
import { TauriMaterialLibraryGateway } from "./platform/tauri/material-library-gateway";
import { TauriSmartEditGateway } from "./platform/tauri/smart-edit-gateway";
import "./styles/global.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Desktop application root is missing");
}

const startupCheck = createDesktopStartupCheck(
  new TauriControlPlaneTransport(),
  new TauriStartupEnvironmentGateway(),
);
const taskSource = new TauriTaskProjectionSource();
const taskCreationGateway = new TauriTaskCreationGateway();
const taskRunControlGateway = new TauriTaskRunControlGateway();
const taskDiscoveryGateway = new TauriTaskDiscoveryGateway();
const taskTargetPreviewSource = new TauriTaskTargetPreviewSource();
const taskTargetResultSource = new TauriTaskTargetResultSource();
const workbenchGateway = new TauriWorkbenchGateway();
const platformAdapter = new TauriPlatformAdapter();
const platformSessionGateway = new TauriPlatformSessionGateway();
const appUpdateGateway = new TauriAppUpdateGateway();
const modelServiceGateway = new TauriModelServiceGateway();
const bilibiliServiceGateway = new TauriBilibiliServiceGateway();
const materialVideoStudioGateway = new TauriMaterialVideoStudioGateway();
const publishWorkspaceGateway = new TauriPublishWorkspaceGateway();
const videoEditingGateway = new TauriVideoEditingGateway();
const materialLibraryGateway = new TauriMaterialLibraryGateway();
const smartEditGateway = new TauriSmartEditGateway();

createRoot(root).render(
  <StrictMode>
    <UnifiedLoginGate client={createUnifiedLoginClient()}>
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
      appUpdateGateway={appUpdateGateway}
      modelServiceGateway={modelServiceGateway}
      bilibiliServiceGateway={bilibiliServiceGateway}
      materialVideoStudioGateway={materialVideoStudioGateway}
      publishWorkspaceGateway={publishWorkspaceGateway}
      videoEditingGateway={videoEditingGateway}
        materialLibraryGateway={materialLibraryGateway}
        smartEditGateway={smartEditGateway}
      />
    </UnifiedLoginGate>
  </StrictMode>,
);
