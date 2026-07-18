import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const desktopE2EMode = "desktop-e2e";
const controlPlaneE2EMode = "control-plane-e2e";

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    {
      name: "desktop-e2e-entrypoint",
      transformIndexHtml: {
        order: "pre",
        handler(html) {
          if (mode !== desktopE2EMode && mode !== controlPlaneE2EMode) {
            return html;
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
    port: 1420,
    strictPort: true,
  },
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
}));
