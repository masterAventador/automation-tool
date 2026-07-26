import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { harnessPort } from "./src/test-harness/harness-port";

const desktopE2EMode = "desktop-e2e";
const controlPlaneE2EMode = "control-plane-e2e";
const browserSettingsE2EMode = "browser-settings-e2e";
const modelServiceE2EMode = "model-service-e2e";

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    {
      name: "desktop-e2e-entrypoint",
      transformIndexHtml: {
        order: "pre",
        handler(html) {
          if (
            mode !== desktopE2EMode &&
            mode !== controlPlaneE2EMode &&
            mode !== browserSettingsE2EMode &&
            mode !== modelServiceE2EMode
          ) {
            return html;
          }
          if (mode === browserSettingsE2EMode) {
            return html.replace(
              "/src/main.tsx",
              "/src/test-browser-settings-main.tsx",
            );
          }
          if (mode === modelServiceE2EMode) {
            return html.replace(
              "/src/main.tsx",
              "/src/test-production-main.ts",
            );
          }
          if (mode === controlPlaneE2EMode) {
            return html.replace(
              "/src/main.tsx",
              "/src/test-control-plane-main.ts",
            );
          }
          return html.replace("/src/main.tsx", "/src/test-tauri-main.tsx");
        },
      },
    },
  ],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    // Derived from the checkout so parallel worktrees do not fight over one
    // port. `strictPort` stays on: silently landing on a different port is how
    // Playwright ends up talking to somebody else's dev server.
    port: harnessPort(process.cwd(), process.env),
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/target/**"],
    },
  },
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
}));
