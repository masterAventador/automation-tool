import { App as AntDesignApp, ConfigProvider } from "antd";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";

import { desktopQueryClient } from "./query-client";
import type { TaskProjectionSource } from "../api/control-plane/task-projections";
import type { WorkbenchGateway } from "../features/workbench/workbench-gateway";
import type { TaskCreationGateway } from "../features/task-create/task-creation-gateway";
import { StartupGate } from "./StartupGate";
import { desktopShellStartupCheck, type StartupCheck } from "./startup";
import { WorkbenchShell } from "./WorkbenchShell";

interface AppProps {
  startupCheck?: StartupCheck;
  queryClient?: QueryClient;
  taskSource?: TaskProjectionSource;
  workbenchGateway?: WorkbenchGateway;
  taskCreationGateway?: TaskCreationGateway;
}

export function App({
  startupCheck = desktopShellStartupCheck,
  queryClient = desktopQueryClient,
  taskSource,
  workbenchGateway,
  taskCreationGateway,
}: AppProps) {
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
          <StartupGate startupCheck={startupCheck}>
            <WorkbenchShell
              taskSource={taskSource}
              gateway={workbenchGateway}
              taskCreationGateway={taskCreationGateway}
            />
          </StartupGate>
        </AntDesignApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
