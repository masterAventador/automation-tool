(() => {
  "use strict";

  if (window.top !== window.self) return;

  // A child WebView shares the native window instead of owning a second
  // window whose Tauri theme can be forced.  Streamlit asks matchMedia during
  // bootstrap and keeps that choice, so answer the two colour-scheme queries
  // before any page script runs.  All other media queries retain the native
  // object and behaviour.
  const nativeMatchMedia = window.matchMedia.bind(window);
  window.matchMedia = (query) => {
    const result = nativeMatchMedia(query);
    const normalized = String(query).replace(/\s+/g, "").toLowerCase();
    if (
      normalized !== "(prefers-color-scheme:dark)" &&
      normalized !== "(prefers-color-scheme:light)"
    ) {
      return result;
    }
    return new Proxy(result, {
      get(target, property) {
        if (property === "matches") return normalized.endsWith(":light)");
        const value = Reflect.get(target, property, target);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
  };

  const PRODUCT_NAME = "智能素材成片";
  const SETTINGS_NAME = "制作服务设置";
  const ROOT_STATE = "data-automation-tool-studio-state";
  const STYLE_ID = "automation-tool-material-video-theme";
  // Upstream's onboarding runs on driver.js, whose stylesheet turns
  // `pointer-events` off for the whole page while a tour is active and back on
  // only for the highlighted element. The product hides the popover, so a user
  // has no way to finish the tour — the page renders and nothing responds.
  // The tour is one-time and asks localStorage whether it was already seen, so
  // answering before the page boots skips it through upstream's own switch.
  // It has to be answered on every launch: the WebUI binds a fresh random port
  // each time, which makes a new origin and an empty localStorage.
  const TOUR_STORAGE_KEY = "stTour-mpt-onboarding-v1";
  const TOUR_ACTIVE_CLASSES = ["driver-active", "driver-fade"];
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
        --automation-tool-primary-strong: #0958d9;
        --automation-tool-primary-soft: rgba(22, 119, 255, 0.12);
        --automation-tool-page: #f3f6fb;
        --automation-tool-surface: #ffffff;
        --automation-tool-text: #182230;
        --automation-tool-muted: #667085;
        --automation-tool-border: #e2e8f2;
        --automation-tool-radius: 12px;
        --automation-tool-control-radius: 8px;
        --automation-tool-font: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      }
      html[${ROOT_STATE}="booting"] body { visibility: hidden !important; }
      html[${ROOT_STATE}="ready"] body,
      html[${ROOT_STATE}="failed"] body { visibility: visible !important; }
      html[${ROOT_STATE}="failed"] body > :not(.automation-tool-studio-failed) {
        visibility: hidden !important;
        pointer-events: none !important;
      }

      /* The onboarding blockade, defused. driver.js writes
         \`.driver-active *{pointer-events:none}\` without \`!important\`, so this
         outranks it however late the tour starts and whatever the class is
         re-applied to. Without it a window whose tour cannot be dismissed is
         simply dead. \`:not(...)\` keeps the fail-closed panel untouched. */
      body.driver-active,
      body.driver-active *:not(.automation-tool-studio-failed *) {
        pointer-events: auto !important;
      }
      :not(body):has(> .driver-active-element) { overflow: visible !important; }

      body, .stApp, [data-testid="stApp"],
      [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        background: var(--automation-tool-page) !important;
        color: var(--automation-tool-text) !important;
        font-family: var(--automation-tool-font) !important;
      }
      [data-testid="stAppViewBlockContainer"],
      [data-testid="stMainBlockContainer"] {
        max-width: 1280px !important;
        padding: 28px 32px 44px !important;
      }
      [data-testid="stHeader"] { background: transparent !important; }

      /* Grouped panels read as cards on the page tint. */
      [data-testid="stVerticalBlockBorderWrapper"],
      [data-testid="stForm"], [data-testid="stExpander"] {
        background: var(--automation-tool-surface) !important;
        border: 1px solid var(--automation-tool-border) !important;
        border-radius: var(--automation-tool-radius) !important;
        box-shadow: 0 4px 18px rgba(24, 34, 48, 0.05) !important;
      }
      [data-testid="stExpander"] summary { color: var(--automation-tool-text) !important; }

      /* The reported defect: a field's caption was dark-theme grey sitting on a
         light page. The sentence a user reads is the paragraph inside. */
      [data-testid="stWidgetLabel"],
      [data-testid="stWidgetLabel"] [data-testid="stMarkdownContainer"],
      [data-testid="stWidgetLabel"] p {
        color: var(--automation-tool-text) !important;
        font-weight: 500 !important;
      }

      /* One box for every kind of field. Streamlit draws the visible border on
         the wrapper and leaves the control inside transparent, so the wrapper
         carries the surface and the control carries the text. */
      [data-testid="stTextInputRootElement"],
      [data-testid="stTextAreaRootElement"],
      [data-testid="stSelectbox"] div[role="group"],
      [data-testid="stNumberInput"] div[role="group"],
      [data-testid="stDateInput"] div[role="group"] {
        background: var(--automation-tool-surface) !important;
        border: 1px solid var(--automation-tool-border) !important;
        border-radius: var(--automation-tool-control-radius) !important;
      }
      [data-testid="stTextInputRootElement"]:focus-within,
      [data-testid="stTextAreaRootElement"]:focus-within,
      [data-testid="stSelectbox"] div[role="group"]:focus-within,
      [data-testid="stNumberInput"] div[role="group"]:focus-within {
        border-color: var(--automation-tool-primary) !important;
        box-shadow: 0 0 0 3px var(--automation-tool-primary-soft) !important;
      }
      input, textarea, select {
        color: var(--automation-tool-text) !important;
        background: transparent !important;
        font-family: var(--automation-tool-font) !important;
      }
      input::placeholder, textarea::placeholder {
        color: var(--automation-tool-muted) !important;
      }

      /* One accent, the product's own, instead of the embedded page's red. */
      [data-testid="stBaseButton-primary"] {
        background: var(--automation-tool-primary) !important;
        border: 1px solid var(--automation-tool-primary) !important;
        color: #ffffff !important;
        border-radius: var(--automation-tool-control-radius) !important;
        font-weight: 500 !important;
      }
      [data-testid="stBaseButton-primary"]:hover {
        background: var(--automation-tool-primary-strong) !important;
        border-color: var(--automation-tool-primary-strong) !important;
      }
      [data-testid="stBaseButton-secondary"] {
        background: var(--automation-tool-surface) !important;
        border: 1px solid var(--automation-tool-border) !important;
        color: var(--automation-tool-text) !important;
        border-radius: var(--automation-tool-control-radius) !important;
      }
      [data-testid="stBaseButton-secondary"]:hover {
        border-color: var(--automation-tool-primary) !important;
        color: var(--automation-tool-primary) !important;
      }
      /* The slider's thumb is the one filled element inside it: the track
         carries \`data-orientation\` and the group carries \`role\`, the thumb
         neither. Addressed by attribute so a restyle upstream cannot repaint
         the whole track by accident. */
      [data-testid="stSlider"] div[data-rac]:not([role]):not([data-orientation]) {
        background: var(--automation-tool-primary) !important;
      }
      /* The filled part of the track is a gradient whose stops carry the
         current value, so it cannot be repainted without losing the reading.
         Rotating its hue keeps the geometry and moves the fill off the
         embedded page's red; the unfilled half is near-grey and barely turns. */
      [data-testid="stSlider"] div[data-rac][data-orientation] > div:not([data-rac]):not([data-testid]) {
        filter: hue-rotate(211deg) !important;
      }
      [data-testid="stSliderThumbValue"] { color: var(--automation-tool-primary) !important; }
      /* The box a checkbox draws is a sibling of the hidden input's wrapper,
         not of the input, so the checked state is read from the label. */
      [data-testid="stCheckbox"] label > div:not([data-testid]) {
        border-radius: 4px !important;
      }
      [data-testid="stCheckbox"] label:has(input:checked) > div:not([data-testid]) {
        background: var(--automation-tool-primary) !important;
        border-color: var(--automation-tool-primary) !important;
      }
      [data-testid="stButtonGroup"] button[aria-checked="true"],
      [data-testid="stButtonGroup"] button[aria-pressed="true"] {
        background: var(--automation-tool-primary) !important;
        border-color: var(--automation-tool-primary) !important;
        color: #ffffff !important;
      }
      a, [data-testid="stMarkdownContainer"] a { color: var(--automation-tool-primary) !important; }

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

  /// Answer upstream's "already seen this?" before the page can ask.
  const suppressOnboardingTour = () => {
    try {
      window.localStorage.setItem(TOUR_STORAGE_KEY, "1");
    } catch {
      // A blocked store only costs the tour its shortcut; the stylesheet above
      // and the class removal below still keep the page usable.
    }
  };

  const removeTour = () => {
    // Hiding the tour is not enough on its own: the class is what disables the
    // page, and it outlives a popover that the user can never reach.
    document.body?.classList.remove(...TOUR_ACTIVE_CLASSES);
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
  suppressOnboardingTour();
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
