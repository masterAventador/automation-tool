import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/**
 * The window the customer actually gets, read from the file that declares it.
 *
 * Every fold-line assertion in `e2e/` is only worth something if the viewport it
 * measures against is the window the product opens at. Keeping that number in a
 * second place is how it stopped being true once already (T96): the shared
 * Playwright config said 800 while every spec ran at 720, so eight months of
 * "above the fold" conclusions were drawn against a fold 80px too high.
 *
 * So the number is not copied here — it is read from
 * `src-tauri/tauri.conf.json`, the file Tauri itself reads. `width`/`height`
 * there are the webview's inner size, which is exactly what a viewport is.
 */

export interface WindowSize {
  readonly width: number;
  readonly height: number;
}

const TAURI_CONFIG_PATH = fileURLToPath(
  new URL("../src-tauri/tauri.conf.json", import.meta.url),
);

/** The `main` window's declaration, or a readable failure if it moved. */
function readMainWindowDeclaration(): Record<string, unknown> {
  const parsed: unknown = JSON.parse(readFileSync(TAURI_CONFIG_PATH, "utf8"));
  const windows = (parsed as { app?: { windows?: unknown } }).app?.windows;
  if (!Array.isArray(windows)) {
    throw new Error(`${TAURI_CONFIG_PATH} 里没有 app.windows 数组`);
  }
  const main = windows.find(
    (window): window is Record<string, unknown> =>
      typeof window === "object" &&
      window !== null &&
      (window as Record<string, unknown>).label === "main",
  );
  if (main === undefined) {
    throw new Error(`${TAURI_CONFIG_PATH} 里没有 label 为 main 的窗口`);
  }
  return main;
}

const MAIN_WINDOW = readMainWindowDeclaration();

function sizeFrom(widthKey: string, heightKey: string): WindowSize {
  const width = MAIN_WINDOW[widthKey];
  const height = MAIN_WINDOW[heightKey];
  if (typeof width !== "number" || typeof height !== "number") {
    throw new Error(
      `${TAURI_CONFIG_PATH} 的 main 窗口没有声明数字 ${widthKey}/${heightKey}`,
    );
  }
  return { width, height };
}

/** The size the main window opens at on first launch. */
export const PRODUCTION_WINDOW: WindowSize = sizeFrom("width", "height");

/** The smallest size the user is allowed to drag the window down to. */
export const MINIMUM_WINDOW: WindowSize = sizeFrom("minWidth", "minHeight");
