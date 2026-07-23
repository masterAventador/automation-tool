import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

const initializationScript = readFileSync(
  resolve(process.cwd(), "src-tauri/src/material_video_studio_init.js"),
  "utf8",
);

const settle = () => new Promise((resolve) => setTimeout(resolve, 320));
const dispose = (dom: JSDOM) => {
  const windowWithDispose = dom.window as unknown as {
    __AUTOMATION_TOOL_MATERIAL_VIDEO_DISPOSE__: () => void;
  };
  windowWithDispose.__AUTOMATION_TOOL_MATERIAL_VIDEO_DISPOSE__();
  dom.window.close();
};

describe("material video studio product shell", () => {
  it("applies the product theme, preserves material settings, and removes upstream identity", async () => {
    const dom = new JSDOM(
      `<!doctype html><html><head><title>Money Printer Turbo</title></head><body>
        <main data-testid="stAppViewContainer">
          <a class="mpt-brand" href="https://github.com/example/MoneyPrinterTurbo">
            <span class="mpt-brand__name">MoneyPrinterTurbo</span>
            <span class="mpt-brand__version">v1.3.2</span>
          </a>
          <div class="st-key-open_settings_dialog_button"><button><span>设置</span></button></div>
          <div role="tablist">
            <button id="model-tab" role="tab" aria-selected="true" aria-controls="model-panel">大模型设置</button>
            <button id="material-tab" role="tab" aria-selected="false" aria-controls="material-panel">素材 API</button>
            <button role="tab">缓存设置</button>
          </div>
          <section id="model-panel" role="tabpanel">模型供应商</section>
          <section id="material-panel" role="tabpanel">Pexels API Key</section>
          <input aria-label="视频主题" />
          <button>生成视频</button>
          <p aria-label="由 Money Printer Turbo 生成">Money Printer Turbo 任务</p>
        </main>
      </body></html>`,
      { runScripts: "outside-only", url: "http://127.0.0.1:32123/studio-test/" },
    );
    let materialTabClicks = 0;
    Object.defineProperty(dom.window, "__AUTOMATION_TOOL_MATERIAL_VIDEO_GUARD_TIMEOUT_MS__", {
      value: 5,
    });
    dom.window.document.getElementById("material-tab")?.addEventListener("click", () => {
      materialTabClicks += 1;
    });

    dom.window.eval(initializationScript);
    await settle();

    const document = dom.window.document;
    expect(document.title).toBe("智能素材成片");
    expect(document.documentElement.getAttribute("data-automation-tool-studio-state")).toBe("ready");
    expect(document.querySelector(".mpt-brand__name")?.textContent).toBe("智能素材成片");
    expect(document.body.textContent).not.toMatch(/money\s*printer\s*turbo/i);
    expect(document.querySelector("[aria-label*='Money']")).toBeNull();
    expect(document.querySelector(".mpt-brand")?.hasAttribute("href")).toBe(false);
    expect(document.querySelector(".st-key-open_settings_dialog_button button")?.getAttribute("aria-label")).toBe("制作服务设置");
    expect(document.querySelector(".st-key-open_settings_dialog_button button")?.textContent).toContain("制作服务设置");
    expect(document.getElementById("model-tab")?.getAttribute("data-automation-tool-hidden")).toBe("true");
    expect(document.getElementById("model-panel")?.getAttribute("data-automation-tool-hidden")).toBe("true");
    expect(document.getElementById("material-panel")?.getAttribute("data-automation-tool-hidden")).toBeNull();
    expect(materialTabClicks).toBeGreaterThan(0);
    expect(document.getElementById("automation-tool-material-video-theme")?.textContent).toContain("#1677ff");

    const lateTask = document.createElement("div");
    lateTask.setAttribute("aria-label", "MoneyPrinterTurbo render job");
    lateTask.textContent = "Money-Printer-Turbo 任务失败";
    document.body.append(lateTask);
    await settle();
    expect(lateTask.textContent).toBe("智能素材成片 任务失败");
    expect(lateTask.getAttribute("aria-label")).toBe("智能素材成片 render job");

    dispose(dom);
  });

  it("fails closed when the embedded page structure is no longer recognized", async () => {
    const dom = new JSDOM("<!doctype html><html><body><p>unknown page</p></body></html>", {
      runScripts: "outside-only",
      url: "http://127.0.0.1:32123/studio-test/",
    });
    Object.defineProperty(dom.window, "__AUTOMATION_TOOL_MATERIAL_VIDEO_GUARD_TIMEOUT_MS__", {
      value: 5,
    });

    dom.window.eval(initializationScript);
    await settle();

    expect(dom.window.document.documentElement.getAttribute("data-automation-tool-studio-state")).toBe("failed");
    expect(dom.window.document.querySelector("[role='alert']")?.textContent).toContain("制作界面暂时不可用");
    dispose(dom);
  });
});
