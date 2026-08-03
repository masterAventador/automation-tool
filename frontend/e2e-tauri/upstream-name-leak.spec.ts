import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";

import { browser } from "@wdio/globals";
import { openWorkbenchSection, waitForStartup } from "./navigation";

/**
 * CQ-02's production-App half.
 *
 * The Playwright harness already walks the rendered accessibility tree,
 * including the publishing edge cases. This spec closes the remaining
 * boundary: it launches the real Tauri App, visits every ordinary workbench
 * page and asks the native WebDriver endpoint for each element's W3C computed
 * role and computed label. Reading aria-label attributes would not be
 * equivalent — a screen reader consumes the computed accessibility result
 * after aria-labelledby, native HTML semantics and hidden state are resolved.
 */

const CAPTURE_FILE = process.env["CQ02_ACCESSIBILITY_CAPTURE"];

const UPSTREAM_NAMES = [
  "moneyprinterturbo",
  "money printer turbo",
  "money-printer-turbo",
  "hyperframes",
  "hyper frames",
  "hyper-frames",
  "browser use",
  "browser_use",
  "browseruse",
  "playwright",
  "chromium",
  "webdriver",
  "official_api",
  "b-roll",
  "poc",
];

/**
 * The legal disclosure is intentionally excluded. It is reached from the foot
 * of 设置, not from the main navigation, and is required to name third-party
 * software. The separate legal-notice acceptance owns that declared exception.
 */
const PAGES = [
  "AI 助理",
  "热点发现",
  "创作",
  "发布",
  "消息与互动",
  "自动化",
  "账号与平台",
  "设置",
];

const ACCESSIBLE_ELEMENTS = [
  "button",
  "a[href]",
  "input",
  "textarea",
  "select",
  "summary",
  "[role]",
  "[aria-label]",
  "[aria-labelledby]",
  "[alt]",
  "[title]",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "nav",
  "main",
  "section",
  "article",
  "dialog",
  "table",
  "th",
  "td",
].join(",");

interface AccessibilityFact {
  readonly role: string;
  readonly label: string;
}

interface PageCapture {
  readonly page: string;
  readonly title: string;
  readonly facts: readonly AccessibilityFact[];
}

const captured: PageCapture[] = [];

function assertNoLeak(value: string, where: string): void {
  const folded = value.toLowerCase();
  for (const name of UPSTREAM_NAMES) {
    assert.ok(
      !folded.includes(name),
      `${where} leaked "${name}" into the production App accessibility result`,
    );
  }
}

async function captureAccessibility(page: string): Promise<void> {
  const title = await browser.execute(() => document.title);
  const elements = await browser.$$(ACCESSIBLE_ELEMENTS);
  const facts: AccessibilityFact[] = [];

  for (let index = 0; index < (await elements.length); index += 1) {
    const element = elements[index]!;
    if (!(await element.isExisting())) continue;

    // These are WebDriver's W3C accessibility endpoints. Do not replace them
    // with DOM attribute reads: doing so would silently stop testing what the
    // assistive-technology user actually hears.
    const [role, label] = await Promise.all([
      element.getComputedRole(),
      element.getComputedLabel(),
    ]);
    if (role !== "" || label !== "") {
      facts.push({ role, label });
    }
  }

  assert.ok(facts.length > 0, `${page} returned no computed accessibility facts`);
  const audible = [title, ...facts.flatMap((fact) => [fact.role, fact.label])].join("\n");
  assertNoLeak(audible, page);
  captured.push({ page, title, facts });
}

after(() => {
  assert.ok(CAPTURE_FILE, "CQ02_ACCESSIBILITY_CAPTURE is required");
  writeFileSync(CAPTURE_FILE, JSON.stringify(captured, null, 2), "utf-8");
});

describe("CQ-02 production App accessibility-tree acceptance", () => {
  before(async () => {
    await browser.refresh();
    await waitForStartup();
  });

  for (const page of PAGES) {
    it(`keeps ${page} free of upstream names`, async () => {
      await openWorkbenchSection(page);
      await captureAccessibility(page);
    });
  }
});
