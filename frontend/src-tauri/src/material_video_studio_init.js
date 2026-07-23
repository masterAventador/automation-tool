(() => {
  "use strict";

  if (window.top !== window.self) return;

  const PRODUCT_NAME = "智能素材成片";
  const SETTINGS_NAME = "制作服务设置";
  const ROOT_STATE = "data-automation-tool-studio-state";
  const STYLE_ID = "automation-tool-material-video-theme";
  const FORBIDDEN = /money\s*[-_ ]?\s*printer\s*[-_ ]?\s*turbo|hyper\s*[-_ ]?\s*frames/gi;
  const FORBIDDEN_AUDIT = /money\s*[-_ ]?\s*printer\s*[-_ ]?\s*turbo|hyper\s*[-_ ]?\s*frames/i;
  const ATTRIBUTE_NAMES = ["aria-label", "aria-description", "title", "alt", "placeholder"];
  let stopped = false;
  let scheduled = false;
  let observer;
  let guardTimer;
  let reconcileTimer;

  const root = () => document.documentElement;

  const replaceBrand = (value) => String(value).replace(FORBIDDEN, PRODUCT_NAME);

  const installTheme = () => {
    if (!root()) return;
    if (!root().hasAttribute(ROOT_STATE)) root().setAttribute(ROOT_STATE, "booting");
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      :root {
        color-scheme: light;
        --automation-tool-primary: #1677ff;
        --automation-tool-page: #f5f7fb;
        --automation-tool-surface: #ffffff;
        --automation-tool-text: #172033;
        --automation-tool-muted: #667085;
        --automation-tool-border: #e4e8f0;
      }
      html[${ROOT_STATE}="booting"] body { visibility: hidden !important; }
      html[${ROOT_STATE}="ready"] body,
      html[${ROOT_STATE}="failed"] body { visibility: visible !important; }
      html[${ROOT_STATE}="failed"] body > :not(.automation-tool-studio-failed) {
        visibility: hidden !important;
        pointer-events: none !important;
      }
      body, .stApp, [data-testid="stAppViewContainer"] {
        background: var(--automation-tool-page) !important;
        color: var(--automation-tool-text) !important;
        font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif !important;
      }
      [data-testid="stAppViewBlockContainer"] {
        max-width: 1280px !important;
        padding: 28px 32px 40px !important;
      }
      [data-testid="stVerticalBlockBorderWrapper"],
      [data-testid="stForm"], [data-testid="stExpander"] {
        background: var(--automation-tool-surface) !important;
        border-color: var(--automation-tool-border) !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 18px rgba(23, 32, 51, 0.05) !important;
      }
      button, input, textarea, [data-baseweb="select"] > div {
        border-radius: 8px !important;
      }
      .stButton button[kind="primary"], button[kind="primary"] {
        background: var(--automation-tool-primary) !important;
        border-color: var(--automation-tool-primary) !important;
      }
      .mpt-brand { display: flex !important; align-items: center !important; }
      .mpt-brand__name { color: var(--automation-tool-text) !important; }
      .mpt-brand__version,
      [data-testid="stToolbar"], [data-testid="stMainMenu"],
      [data-testid="stStatusWidget"], .stDeployButton,
      .driver-popover, .driver-overlay,
      iframe[title*="streamlit-tour" i] { display: none !important; }
      .st-key-task_manager_entry { display: none !important; }
      .driver-active-element { pointer-events: auto !important; }
      [data-automation-tool-hidden="true"] { display: none !important; }
      .automation-tool-studio-failed {
        position: fixed;
        inset: 0;
        z-index: 2147483647;
        box-sizing: border-box;
        display: grid;
        align-content: center;
        justify-items: center;
        padding: 48px;
        background: var(--automation-tool-page);
        color: var(--automation-tool-text);
        font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
        visibility: visible !important;
      }
      .automation-tool-studio-failed h1 { margin: 0 0 12px; font-size: 22px; }
      .automation-tool-studio-failed p { margin: 0; color: var(--automation-tool-muted); }
    `;
    (document.head || root()).appendChild(style);
  };

  const failClosed = (reason = "unexpected") => {
    if (stopped) return;
    stopped = true;
    observer?.disconnect();
    if (document.title !== PRODUCT_NAME) document.title = PRODUCT_NAME;
    root()?.setAttribute(ROOT_STATE, "failed");
    root()?.setAttribute("data-automation-tool-studio-failure", reason);
    if (document.body) {
      const panel = document.createElement("main");
      panel.className = "automation-tool-studio-failed";
      panel.setAttribute("role", "alert");
      const heading = document.createElement("h1");
      heading.textContent = "制作界面暂时不可用";
      const detail = document.createElement("p");
      detail.textContent = "页面组件需要更新，请关闭窗口后重试。";
      panel.append(heading, detail);
      document.querySelector(".automation-tool-studio-failed")?.remove();
      root()?.append(panel);
    }
  };

  const removeTour = () => {
    document.querySelectorAll(".driver-popover, .driver-overlay").forEach((node) => {
      node.setAttribute("data-automation-tool-hidden", "true");
      node.setAttribute("aria-hidden", "true");
    });
    document.querySelectorAll('iframe[title*="streamlit-tour" i]').forEach((frame) => {
      const component = frame.closest('[data-testid="stCustomComponentV1"]');
      (component || frame).setAttribute("data-automation-tool-hidden", "true");
      (component || frame).setAttribute("aria-hidden", "true");
    });
  };

  const sanitizeTextAndAccessibility = () => {
    if (!document.body) return;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      if (["SCRIPT", "STYLE", "NOSCRIPT"].includes(node.parentElement?.tagName || "")) continue;
      const next = replaceBrand(node.nodeValue || "");
      if (next !== node.nodeValue) node.nodeValue = next;
    }
    document.querySelectorAll("*").forEach((element) => {
      for (const name of ATTRIBUTE_NAMES) {
        if (!element.hasAttribute(name)) continue;
        const current = element.getAttribute(name) || "";
        const next = replaceBrand(current);
        if (next !== current) element.setAttribute(name, next);
      }
    });
  };

  const removeExternalNavigation = () => {
    document.querySelectorAll("a[href]").forEach((anchor) => {
      let external;
      try {
        const destination = new URL(anchor.getAttribute("href"), window.location.href);
        external = destination.origin !== window.location.origin;
      } catch {
        external = true;
      }
      if (!external) return;
      anchor.removeAttribute("href");
      anchor.removeAttribute("target");
      anchor.removeAttribute("rel");
      anchor.setAttribute("role", "text");
    });
  };

  const productizeHeaderAndSettings = () => {
    document.querySelectorAll(".mpt-brand__name").forEach((name) => {
      if (name.textContent !== PRODUCT_NAME) name.textContent = PRODUCT_NAME;
    });
    document.querySelectorAll(".mpt-brand__version").forEach((version) => {
      version.setAttribute("data-automation-tool-hidden", "true");
      version.setAttribute("aria-hidden", "true");
    });

    document.querySelectorAll(".st-key-open_settings_dialog_button button").forEach((button) => {
      button.setAttribute("aria-label", SETTINGS_NAME);
      const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        if ((walker.currentNode.nodeValue || "").trim() === "设置") {
          walker.currentNode.nodeValue = SETTINGS_NAME;
        }
      }
    });

    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const modelTab = tabs.find((tab) => /大模型设置|LLM\s*Settings/i.test(tab.textContent || ""));
    const materialTab = tabs.find((tab) => /素材\s*API|Material\s*API/i.test(tab.textContent || ""));
    if (modelTab) {
      modelTab.setAttribute("data-automation-tool-hidden", "true");
      modelTab.setAttribute("aria-hidden", "true");
      modelTab.setAttribute("tabindex", "-1");
      const panelId = modelTab.getAttribute("aria-controls");
      if (panelId) {
        const panel = document.getElementById(panelId);
        panel?.setAttribute("data-automation-tool-hidden", "true");
        panel?.setAttribute("aria-hidden", "true");
      }
      if (
        modelTab.getAttribute("aria-selected") === "true" &&
        !modelTab.hasAttribute("data-automation-tool-redirected") &&
        materialTab instanceof HTMLElement
      ) {
        modelTab.setAttribute("data-automation-tool-redirected", "true");
        materialTab.click();
      }
    }
  };

  const hasRequiredStructure = () => Boolean(
    document.querySelector('[data-testid="stAppViewContainer"]') &&
    document.querySelector('input[aria-label*="视频主题"], input[aria-label*="Video Subject" i]') &&
    [...document.querySelectorAll("button")].some((button) => /生成视频|Generate Video/i.test(button.textContent || "")),
  );

  const audit = () => {
    if (!document.body) return null;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      if (["SCRIPT", "STYLE", "NOSCRIPT"].includes(walker.currentNode.parentElement?.tagName || "")) continue;
      if (FORBIDDEN_AUDIT.test(walker.currentNode.nodeValue || "")) return "text";
    }
    for (const element of document.querySelectorAll("*")) {
      for (const name of ATTRIBUTE_NAMES) {
        if (FORBIDDEN_AUDIT.test(element.getAttribute(name) || "")) return `attribute_${name}`;
      }
      if (element instanceof HTMLAnchorElement && element.hasAttribute("href")) {
        try {
          const destination = new URL(element.getAttribute("href"), window.location.href);
          if (destination.origin !== window.location.origin) return "external_link";
        } catch {
          return "invalid_link";
        }
      }
    }
    return null;
  };

  const reconcile = () => {
    scheduled = false;
    if (stopped) return;
    observer?.disconnect();
    try {
      installTheme();
      if (document.title !== PRODUCT_NAME) document.title = PRODUCT_NAME;
      removeTour();
      sanitizeTextAndAccessibility();
      removeExternalNavigation();
      productizeHeaderAndSettings();
      const violation = audit();
      if (violation) return failClosed(`content_policy_${violation}`);
      if (hasRequiredStructure()) root()?.setAttribute(ROOT_STATE, "ready");
    } catch {
      failClosed("initialization_error");
    } finally {
      if (!stopped && observer && root()) {
        observer.observe(root(), { childList: true, subtree: true });
      }
    }
  };

  const schedule = () => {
    if (scheduled || stopped) return;
    scheduled = true;
    reconcileTimer = window.setTimeout(reconcile, 250);
  };

  installTheme();
  window.open = () => null;
  observer = new MutationObserver(schedule);
  if (root()) observer.observe(root(), { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", schedule, { once: true });
  reconcile();

  const timeout = Number(window.__AUTOMATION_TOOL_MATERIAL_VIDEO_GUARD_TIMEOUT_MS__) || 120_000;
  guardTimer = window.setTimeout(() => {
    if (!stopped && root()?.getAttribute(ROOT_STATE) !== "ready") failClosed("structure_timeout");
  }, timeout);
  const dispose = () => {
    stopped = true;
    observer?.disconnect();
    window.clearTimeout(guardTimer);
    window.clearTimeout(reconcileTimer);
  };
  window.addEventListener("pagehide", dispose, { once: true });
  Object.defineProperty(window, "__AUTOMATION_TOOL_MATERIAL_VIDEO_DISPOSE__", {
    value: dispose,
    configurable: true,
  });
})();
