import "@wdio/tauri-plugin";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { desktopShellStartupCheck } from "./app/startup";
import { TauriAppUpdateGateway } from "./platform/tauri/app-update-gateway";
import "./styles/global.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Desktop test application root is missing");
}

const appUpdateGateway = new TauriAppUpdateGateway();

createRoot(root).render(
  <StrictMode>
    <App startupCheck={desktopShellStartupCheck} appUpdateGateway={appUpdateGateway} />
  </StrictMode>,
);
