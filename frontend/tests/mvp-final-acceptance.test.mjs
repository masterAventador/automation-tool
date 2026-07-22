import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const reportUrl = new URL("contracts/quality/mvp-final-acceptance.v1.json", repositoryRoot);

const EXPECTED_BLOCKERS = {
  "MVP-AC-01": ["P9-06", "P9-07"],
  "MVP-AC-04": ["B5-15", "P9-06", "P9-07"],
  "MVP-AC-05": ["D6-16", "P9-06", "P9-07"],
  "MVP-AC-06": ["A7-16", "A7-17", "P9-06", "P9-07"],
  "MVP-AC-07": ["A7-16", "A7-17", "P9-06", "P9-07"],
  "MVP-AC-13": ["P9-06", "P9-07"],
  "MVP-AC-14": ["P9-05"],
};

test("P9-09 binds all 14 product criteria to an honest final acceptance result", async () => {
  const [reportSource, reviewSource, productPlan, roadmap] = await Promise.all([
    readFile(reportUrl, "utf8"),
    readFile(
      new URL("contracts/quality/mvp-spec-review.v1.json", repositoryRoot),
      "utf8",
    ),
    readFile(new URL("docs/product-plan.md", repositoryRoot), "utf8"),
    readFile(new URL("docs/development-roadmap.md", repositoryRoot), "utf8"),
  ]);
  const report = JSON.parse(reportSource);
  const review = JSON.parse(reviewSource);
  const productCriteria = productPlan
    .split("### 4.5 MVP 验收标准")[1]
    ?.split("## 5.")[0]
    ?.split("\n")
    .map((line) => line.match(/^\d+\. (.+)[；。]$/u)?.[1])
    .filter(Boolean);

  assert.equal(report.version, "mvp.final-acceptance.v1");
  assert.equal(report.scope, "p9.local-single-device-mvp");
  assert.equal(report.overallResult, "pending_external_acceptance");
  assert.deepEqual(report.summary, {
    verifiedAutomated: 7,
    pendingRealPlatform: 4,
    pendingDevicePackage: 3,
  });
  assert.equal(report.criteria.length, 14);
  assert.deepEqual(
    report.criteria.map(({ id }) => id),
    Array.from({ length: 14 }, (_, index) =>
      `MVP-AC-${String(index + 1).padStart(2, "0")}`,
    ),
  );
  assert.deepEqual(
    report.criteria.map(({ criterion }) => criterion),
    productCriteria,
  );
  assert.deepEqual(
    report.criteria.map(({ criterion }) => criterion),
    review.acceptanceCriteria.map(({ criterion }) => criterion),
  );
  assert.match(
    roadmap,
    /\| P9-09 \| 本地 MVP 最终验收 \|[^\n]+\| 🔍 待验收 \|/u,
  );

  const resultCounts = Object.groupBy(report.criteria, ({ result }) => result);
  assert.equal(resultCounts.verified_automated?.length, 7);
  assert.equal(resultCounts.pending_real_platform?.length, 4);
  assert.equal(resultCounts.pending_device_package?.length, 3);

  for (const item of report.criteria) {
    assert.ok(item.evidence.length >= 1);
    const expectedBlockers = EXPECTED_BLOCKERS[item.id] ?? [];
    assert.deepEqual(item.blockingTasks, expectedBlockers);
    if (item.result === "verified_automated") {
      assert.deepEqual(item.blockingTasks, []);
    } else {
      assert.ok(item.blockingTasks.length > 0);
    }
    for (const evidence of item.evidence) {
      const source = await readFile(new URL(evidence.path, repositoryRoot), "utf8");
      assert.ok(source.includes(evidence.anchor), `${evidence.path} evidence drifted`);
    }
    for (const blocker of item.blockingTasks) {
      const row = roadmap.split("\n").find((line) => line.startsWith(`| ${blocker} |`));
      assert.ok(row, `${blocker} is absent from the roadmap`);
      assert.ok(!row.includes("✅ 已完成"), `${blocker} cannot remain a blocker when complete`);
    }
  }
});

test("P9-09 keeps customer Demo account prerequisites separate from local MVP", async () => {
  const report = JSON.parse(await readFile(reportUrl, "utf8"));
  assert.deepEqual(
    report.customerDemoPrerequisites,
    Array.from({ length: 6 }, (_, index) => `U9-0${index + 1}`),
  );
  assert.equal(report.externalSideEffectsPerformed, false);
  assert.equal(report.visibleDeviceAcceptancePerformed, false);
});
