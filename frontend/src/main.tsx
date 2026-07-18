import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { createTransportStartupCheck } from "./app/startup";
import { TauriControlPlaneTransport } from "./platform/tauri/control-plane-transport";
import "./styles/global.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Desktop application root is missing");
}

const startupCheck = createTransportStartupCheck(new TauriControlPlaneTransport());

createRoot(root).render(
  <StrictMode>
    <App startupCheck={startupCheck} />
  </StrictMode>,
);
