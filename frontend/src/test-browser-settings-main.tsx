import "@wdio/tauri-plugin";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import type { StartupCheck } from "./app/startup";
import { TauriPlatformAdapter } from "./platform/tauri/platform-adapter";
import "./styles/global.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("Browser settings acceptance root is missing");
}

const readyStartup: StartupCheck = {
  async check() {
    return { status: "ready" };
  },
};

createRoot(root).render(
  <StrictMode>
    <App
      startupCheck={readyStartup}
      platformAdapter={new TauriPlatformAdapter()}
    />
  </StrictMode>,
);
