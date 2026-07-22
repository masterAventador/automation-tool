import { App as AntDesignApp, ConfigProvider, Space } from "antd";
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
import { BrowserSettings } from "../features/settings/BrowserSettings";
import { Diagnostics } from "../features/diagnostics/Diagnostics";
import type { AppUpdateGateway } from "../features/app-updates/contracts";
import { AccountSessionGate } from "../features/account-session/AccountSessionGate";
import type { AccountSessionGateway } from "../features/account-session/account-session-gateway";

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
    />
  );
  const desktopApplication = (
    <StartupGate
      startupCheck={startupCheck}
      repairTools={
        platformAdapter === undefined ? undefined : (
          <Space orientation="vertical" size="large" className="settings-stack">
            <BrowserSettings platform={platformAdapter} />
            <Diagnostics platform={platformAdapter} />
          </Space>
        )
      }
    >
      {workbench}
    </StartupGate>
  );
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        theme={{
          token: {
            colorPrimary: "#2f6fed",
            borderRadius: 8,
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
