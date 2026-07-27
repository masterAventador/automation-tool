#!/usr/bin/env node
// PC-17: measure every frozen slot's pixel budget in the packaged Chromium.
//
// The budget is a container's usable width and the font size drawn in it — not a
// character count. Nine characters is 9em of Han, 2.3em of `iiiiiiiii` and 12em
// of `WWWWWWWWW`, so a limit expressed in characters is three different limits
// depending on what gets written.
//
// Two things this probe learned the hard way, both visible in what it emits:
//
//   * Measure after seeking the part's timeline to its end. Measuring at load
//     caught the animation mid-flight — one container reported -13px of usable
//     width, an element still scaled toward zero, and a budget taken then
//     describes a frame nobody sees.
//   * Record whether the slot already overflows vertically with its *original*
//     copy. 14 of the 48 do, by design: a masked reveal clips its text on
//     purpose. So PC-14's overflow test cannot ask "does it overflow" — only
//     "does it overflow more than it did", and that needs this baseline.
// Reports, per slot: the container's usable width in CSS pixels, the font size,
// and whether the element currently overflows.
import { chromium } from "@playwright/test";
import { readFileSync } from "node:fs";

const [, , entryPath, slotsJson] = process.argv;
const slots = JSON.parse(slotsJson);
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
await page.goto(`file://${entryPath}`);
await page.waitForTimeout(400);
// Seek the part's own timeline to its end before measuring.
//
// Measuring at load caught the animation mid-flight: 14 of the 48 slots
// reported vertical overflow with their *original* copy, and one container
// measured -13px of usable width — an element still scaled toward zero. A
// pixel budget taken then describes a frame nobody sees.
//
// The seek is the same call the render Worker makes; a part that does not
// register a timeline is measured as it loads, which is what it is.
await page.evaluate(() => {
  const timelines = window.__timelines;
  if (!timelines) return;
  for (const timeline of Object.values(timelines)) {
    if (typeof timeline.seek === "function") {
      timeline.seek(typeof timeline.duration === "function" ? timeline.duration() : 999);
    }
  }
});
await page.waitForTimeout(200);
const measured = await page.evaluate((indices) => {
  const walker = document.createTreeWalker(document, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  return indices.map((wanted) => {
    const node = nodes.find((n) => n.nodeValue.trim() === wanted.original);
    if (!node) return { index: wanted.index, found: false };
    const element = node.parentElement;
    const style = getComputedStyle(element);
    const box = element.getBoundingClientRect();
    const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    return {
      index: wanted.index,
      found: true,
      usableWidth: Math.round(box.width - padding),
      fontSize: Math.round(parseFloat(style.fontSize)),
      overflowsX: element.scrollWidth > element.clientWidth + 1,
      overflowsY: element.scrollHeight > element.clientHeight + 1,
    };
  });
}, slots);
console.log(JSON.stringify(measured));
await browser.close();
