import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

const initializationScript = readFileSync(
  resolve(process.cwd(), "src-tauri/src/material_video_studio_init.js"),
  "utf8",
);

const settle = () => new Promise((resolve) => setTimeout(resolve, 320));
const executeInitialization = (dom: JSDOM) => {
  Object.defineProperty(dom.window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (media: string): MediaQueryList => ({
      matches: false,
      media,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
    }),
  });
  dom.window.eval(initializationScript);
};
const dispose = (dom: JSDOM) => {
  const windowWithDispose = dom.window as unknown as {
    __AUTOMATION_TOOL_MATERIAL_VIDEO_DISPOSE__: () => void;
  };
  windowWithDispose.__AUTOMATION_TOOL_MATERIAL_VIDEO_DISPOSE__();
  dom.window.close();
};

// Upstream's onboarding tour is driver.js, whose own stylesheet switches the
// whole page off while a tour is running and switches only the highlighted
// element back on. Reproduced verbatim from the shipped bundle
// (streamlit_tour/frontend/build/index-*.js) so these tests fail the same way
// the real page did rather than the way we imagine it did. Neither rule is
// `!important`, which is what lets the product stylesheet win.
const DRIVER_BLOCKADE_STYLESHEET = `
  .driver-active .driver-overlay,.driver-active *{pointer-events:none}
  .driver-active .driver-active-element,.driver-active .driver-active-element *,.driver-popover,.driver-popover *{pointer-events:auto}
`;

// The markup below is the shape Streamlit 1.59 actually renders, read off the
// running WebUI rather than guessed: the visible box of a text field is the
// root element, the input inside it is transparent, and the label is a
// `stWidgetLabel`. Widgets are addressed by `data-testid` because that is the
// hook Streamlit maintains for automation.
const streamlitPage = (bodyClass = "") =>
  `<!doctype html><html><head><title>Money Printer Turbo</title>
     <style>${DRIVER_BLOCKADE_STYLESHEET}</style>
   </head><body class="${bodyClass}">
    <div data-testid="stAppViewContainer">
      <div data-testid="stAppViewBlockContainer">
        <div data-testid="stTextInput" class="stTextInput">
          <label data-testid="stWidgetLabel"><span><div data-testid="stMarkdownContainer"><p>视频主题（AI 根据主题生成视频文案）</p></div></span></label>
          <div class="react-aria-TextField">
            <div data-testid="stTextInputRootElement"><input aria-label="视频主题" /></div>
          </div>
        </div>
        <div data-testid="stTextArea">
          <div data-testid="stTextAreaRootElement"><textarea aria-label="视频文案"></textarea></div>
        </div>
        <div data-testid="stSelectbox" class="stSelectbox">
          <div class="react-aria-ComboBox">
            <div role="group"><input role="combobox" aria-label="生成视频脚本的语言" value="中文" /></div>
          </div>
        </div>
        <button data-testid="stBaseButton-primary">生成视频</button>
      </div>
    </div>
  </body></html>`;

const boot = (dom: JSDOM) => {
  Object.defineProperty(dom.window, "__AUTOMATION_TOOL_MATERIAL_VIDEO_GUARD_TIMEOUT_MS__", {
    value: 5,
  });
  executeInitialization(dom);
};

const styleOf = (dom: JSDOM, selector: string) => {
  const element = dom.window.document.querySelector(selector);
  if (!element) throw new Error(`missing element for ${selector}`);
  return dom.window.getComputedStyle(element);
};

// jsdom does not resolve `var()` when it computes a style, so asking it for the
// background of a field returns transparent no matter what the sheet says.
// Rather than inline the palette at every call site to suit the test runner,
// these read the injected sheet through the CSSOM — the rule must exist, must
// set the property, and the token it names must be defined — and join the two
// halves the way a browser would. What that still cannot show is how the page
// looks once Streamlit's own styles are in the cascade; that is checked against
// the running WebUI and recorded in `docs/development/T108.md`.
const injectedRules = (dom: JSDOM) => {
  const sheet = [...dom.window.document.styleSheets].find(
    (candidate) => candidate.ownerNode instanceof dom.window.HTMLStyleElement
      && candidate.ownerNode.id === "automation-tool-material-video-theme",
  );
  if (!sheet) throw new Error("the product stylesheet was never injected");
  return [...sheet.cssRules].filter(
    (rule): rule is CSSStyleRule => rule instanceof dom.window.CSSStyleRule,
  );
};

const themeToken = (dom: JSDOM, name: string) => {
  const root = injectedRules(dom).find((rule) => rule.selectorText.includes(":root"));
  const value = root?.style.getPropertyValue(name).trim();
  if (!value) throw new Error(`the theme declares no ${name}`);
  return value;
};

/** The value a control ends up with, after the token it names is substituted. */
const resolvedStyle = (dom: JSDOM, selectorFragment: string, property: string) => {
  const rule = injectedRules(dom).find(
    (candidate) => candidate.selectorText.includes(selectorFragment)
      && candidate.style.getPropertyValue(property).trim() !== "",
  );
  if (!rule) throw new Error(`no rule sets ${property} for ${selectorFragment}`);
  const declared = rule.style.getPropertyValue(property).trim();
  return declared.replace(/var\((--[a-z-]+)\)/gi, (_, name: string) => themeToken(dom, name));
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

    executeInitialization(dom);
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

    executeInitialization(dom);
    await settle();

    expect(dom.window.document.documentElement.getAttribute("data-automation-tool-studio-state")).toBe("failed");
    expect(dom.window.document.querySelector("[role='alert']")?.textContent).toContain("制作界面暂时不可用");
    dispose(dom);
  });

  it("never lets upstream's onboarding tour start, because the product hides its only exit", async () => {
    const dom = new JSDOM(streamlitPage(), {
      runScripts: "outside-only",
      url: "http://127.0.0.1:32123/studio-test/",
    });

    boot(dom);
    await settle();

    // The tour is one-time and asks localStorage whether it has already been
    // seen. Answering before the page boots is upstream's own supported way to
    // skip it, and it has to happen every launch: the WebUI binds to a fresh
    // random port each time, so its origin — and with it localStorage — is new.
    expect(dom.window.localStorage.getItem("stTour-mpt-onboarding-v1")).toBe("1");
    dispose(dom);
  });

  it("keeps every control clickable even if the tour blockade is applied anyway", async () => {
    // Booting with the class already on <body> is the state the user hit: the
    // page renders normally and nothing responds, because the product hides the
    // popover that would otherwise let a user finish the tour and release it.
    const dom = new JSDOM(streamlitPage("driver-active driver-fade"), {
      runScripts: "outside-only",
      url: "http://127.0.0.1:32123/studio-test/",
    });

    boot(dom);
    await settle();

    expect(dom.window.document.body.classList.contains("driver-active")).toBe(false);
    expect(styleOf(dom, 'input[aria-label="视频主题"]').pointerEvents).toBe("auto");
    expect(styleOf(dom, '[data-testid="stBaseButton-primary"]').pointerEvents).toBe("auto");

    // Stripping the class is not enough on its own: driver.js re-applies it on
    // its next tick. The product stylesheet has to out-rank the blockade, so
    // the window can never be left dead.
    dom.window.document.body.classList.add("driver-active");
    expect(styleOf(dom, 'input[aria-label="视频主题"]').pointerEvents).toBe("auto");
    expect(styleOf(dom, '[data-testid="stBaseButton-primary"]').pointerEvents).toBe("auto");
    dispose(dom);
  });

  it("gives every control the product's own surface, text and accent colors", async () => {
    const dom = new JSDOM(streamlitPage(), {
      runScripts: "outside-only",
      url: "http://127.0.0.1:32123/studio-test/",
    });

    boot(dom);
    await settle();

    // The palette is the product's own, not a second one invented for this
    // window: these are the values frontend/src/styles/global.css uses.
    expect(themeToken(dom, "--automation-tool-page")).toBe("#f3f6fb");
    expect(themeToken(dom, "--automation-tool-surface")).toBe("#ffffff");
    expect(themeToken(dom, "--automation-tool-text")).toBe("#182230");
    expect(themeToken(dom, "--automation-tool-primary")).toBe("#1677ff");

    // Fields the user can read: a white box on the page tint, product text.
    expect(resolvedStyle(dom, '[data-testid="stTextInputRootElement"]', "background")).toBe("#ffffff");
    expect(resolvedStyle(dom, '[data-testid="stTextAreaRootElement"]', "background")).toBe("#ffffff");
    // A dropdown is a box like any other field; it used to be the odd one out.
    expect(resolvedStyle(dom, '[data-testid="stSelectbox"] div[role="group"]', "background")).toBe("#ffffff");
    expect(resolvedStyle(dom, "input, textarea, select", "color")).toBe("#182230");

    // The reported defect: captions were dark-theme grey sitting on a light
    // page. The sentence a user actually reads is the paragraph inside.
    expect(resolvedStyle(dom, '[data-testid="stWidgetLabel"] p', "color")).toBe("#182230");

    // One accent, the product's own, on the action that submits the work.
    expect(resolvedStyle(dom, '[data-testid="stBaseButton-primary"]', "background")).toBe("#1677ff");
    expect(resolvedStyle(dom, '[data-testid="stAppViewContainer"]', "background")).toBe("#f3f6fb");

    // The page tint does reach the document without a token, so the browser's
    // own computation is worth asserting where jsdom can do it.
    expect(styleOf(dom, '[data-testid="stWidgetLabel"] p').fontWeight).toBe("500");
    dispose(dom);
  });
});
