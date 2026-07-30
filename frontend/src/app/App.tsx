import { App as AntDesignApp, ConfigProvider, Space } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";

import { desktopQueryClient } from "./query-client";
import type { TaskProjectionSource } from "../api/control-plane/task-projections";
import type { TaskTargetPreviewSource } from "../api/control-plane/task-target-previews";
import type { TaskTargetResultSource } from "../api/control-plane/task-target-results";
import type { WorkbenchGateway } from "../features/workbench/workbench-gateway";
import type { TaskCreationGateway } from "../features/task-create/task-creation-gateway";
import type { TaskRunControlGateway } from "../features/task-runs/task-run-controls";
import type { TaskDiscoveryGateway } from "../features/task-runs/task-discovery";
import type { PlatformAdapter } from "../platform/types";
import type { PlatformSessionGateway } from "../features/platform-sessions/platform-session-gateway";
import { StartupGate } from "./StartupGate";
import type { StartupCheck } from "./startup";
import { WorkbenchShell } from "./WorkbenchShell";
import { Diagnostics } from "../features/diagnostics/Diagnostics";
import { AppUpdateCenter } from "../features/app-updates/AppUpdateCenter";
import type { AppUpdateGateway } from "../features/app-updates/contracts";
import { AccountSessionGate } from "../features/account-session/AccountSessionGate";
import type { AccountSessionGateway } from "../features/account-session/account-session-gateway";
import type { ModelServiceGateway } from "../features/settings/model-service-gateway";
import { ModelServiceSettings } from "../features/settings/ModelServiceSettings";
import type { MaterialVideoStudioGateway } from "../features/video-studio/material-video-studio-gateway";
import type { VideoEditingGateway } from "../features/video-editing/video-editing-gateway";
import type { SelectedVideo } from "../features/publishing/PublishWorkspace";
import type { PublishWorkspaceGateway } from "../features/publishing/publish-workspace-gateway";

interface AppProps {
  startupCheck: StartupCheck;
  queryClient?: QueryClient;
  taskSource?: TaskProjectionSource;
  workbenchGateway?: WorkbenchGateway;
  taskCreationGateway?: TaskCreationGateway;
  taskRunControlGateway?: TaskRunControlGateway;
  taskDiscoveryGateway?: TaskDiscoveryGateway;
  taskTargetPreviewSource?: TaskTargetPreviewSource;
  taskTargetResultSource?: TaskTargetResultSource;
  platformAdapter?: PlatformAdapter;
  platformSessionGateway?: PlatformSessionGateway;
  appUpdateGateway?: AppUpdateGateway;
  accountSessionGateway?: AccountSessionGateway;
  modelServiceGateway?: ModelServiceGateway;
  materialVideoStudioGateway?: MaterialVideoStudioGateway;
  videoEditingGateway?: VideoEditingGateway;
  publishWorkspaceGateway?: PublishWorkspaceGateway | undefined;
  selectedVideo?: SelectedVideo | undefined;
}

export function App({
  startupCheck,
  queryClient = desktopQueryClient,
  taskSource,
  workbenchGateway,
  taskCreationGateway,
  taskRunControlGateway,
  taskDiscoveryGateway,
  taskTargetPreviewSource,
  taskTargetResultSource,
  platformAdapter,
  platformSessionGateway,
  appUpdateGateway,
  accountSessionGateway,
  modelServiceGateway,
  materialVideoStudioGateway,
  videoEditingGateway,
  publishWorkspaceGateway,
  selectedVideo,
}: AppProps) {
  const workbench = (
    <WorkbenchShell
      taskSource={taskSource}
      gateway={workbenchGateway}
      taskCreationGateway={taskCreationGateway}
      taskRunControlGateway={taskRunControlGateway}
      taskDiscoveryGateway={taskDiscoveryGateway}
      taskTargetPreviewSource={taskTargetPreviewSource}
      taskTargetResultSource={taskTargetResultSource}
      platformAdapter={platformAdapter}
      platformSessionGateway={platformSessionGateway}
      appUpdateGateway={appUpdateGateway}
      modelServiceGateway={modelServiceGateway}
      materialVideoStudioGateway={materialVideoStudioGateway}
      videoEditingGateway={videoEditingGateway}
      publishWorkspaceGateway={publishWorkspaceGateway}
      selectedVideo={selectedVideo}
    />
  );
  const desktopApplication = (
    <StartupGate
      startupCheck={startupCheck}
      repairTools={
        platformAdapter === undefined && appUpdateGateway === undefined ? undefined : (
          <Space orientation="vertical" size="large" className="settings-stack">
            {appUpdateGateway === undefined ? null : (
              <AppUpdateCenter gateway={appUpdateGateway} showSettings />
            )}
            {modelServiceGateway === undefined ? null : (
              <ModelServiceSettings gateway={modelServiceGateway} />
            )}
            {platformAdapter === undefined ? null : (
              <Diagnostics platform={platformAdapter} />
            )}
          </Space>
        )
      }
      updateCenter={
        appUpdateGateway === undefined ? undefined : (
          <AppUpdateCenter gateway={appUpdateGateway} showSettings={false} />
        )
      }
    >
      {workbench}
    </StartupGate>
  );
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: "#13a8ad",
            colorInfo: "#13a8ad",
            colorSuccess: "#2e9a72",
            colorBgLayout: "#f6f5f0",
            colorText: "#17212b",
            borderRadius: 10,
            fontFamily:
              '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif',
          },
        }}
      >
        <AntDesignApp>
          {accountSessionGateway === undefined ? (
            desktopApplication
          ) : (
            <AccountSessionGate gateway={accountSessionGateway}>
              {desktopApplication}
            </AccountSessionGate>
          )}
        </AntDesignApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
