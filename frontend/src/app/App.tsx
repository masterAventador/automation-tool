import { App as AntDesignApp, ConfigProvider } from "antd";

import { StartupGate } from "./StartupGate";
import { desktopShellStartupCheck, type StartupCheck } from "./startup";
import { WorkbenchShell } from "./WorkbenchShell";

interface AppProps {
  startupCheck?: StartupCheck;
}

export function App({ startupCheck = desktopShellStartupCheck }: AppProps) {
  return (
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
          <WorkbenchShell />
        </StartupGate>
      </AntDesignApp>
    </ConfigProvider>
  );
}
