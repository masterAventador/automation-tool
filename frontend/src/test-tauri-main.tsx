import "@wdio/tauri-plugin";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import "./styles/global.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Desktop test application root is missing");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
