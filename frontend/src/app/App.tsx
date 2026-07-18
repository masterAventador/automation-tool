import { App as AntDesignApp, ConfigProvider } from "antd";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";

import { desktopQueryClient } from "./query-client";
import type { TaskProjectionSource } from "../api/control-plane/task-projections";
import type { WorkbenchGateway } from "../features/workbench/workbench-gateway";
import type { TaskCreationGateway } from "../features/task-create/task-creation-gateway";
import type { TaskRunControlGateway } from "../features/task-runs/task-run-controls";
import { StartupGate } from "./StartupGate";
import { desktopShellStartupCheck, type StartupCheck } from "./startup";
import { WorkbenchShell } from "./WorkbenchShell";

interface AppProps {
  startupCheck?: StartupCheck;
  queryClient?: QueryClient;
  taskSource?: TaskProjectionSource;
  workbenchGateway?: WorkbenchGateway;
  taskCreationGateway?: TaskCreationGateway;
  taskRunControlGateway?: TaskRunControlGateway;
}

export function App({
  startupCheck = desktopShellStartupCheck,
  queryClient = desktopQueryClient,
  taskSource,
  workbenchGateway,
  taskCreationGateway,
  taskRunControlGateway,
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
              taskRunControlGateway={taskRunControlGateway}
            />
          </StartupGate>
        </AntDesignApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
