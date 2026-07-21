import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const reviewUrl = new URL("contracts/quality/mvp-spec-review.v1.json", repositoryRoot);

const ACCEPTANCE_FRAGMENTS = [
  "全新安装后打开 App，不出现产品注册或登录页面",
  "App 自动完成 Control Plane 连通性、Local Executor、浏览器和数据目录诊断",
  "未登录抖音时，任务进入“等待平台登录”，并拉起可见的外部浏览器",
  "用户扫码一次后，App 能检测登录并在下一次启动复用平台登录态",
  "用户能创建关键词任务，看到真实目标预览并排除目标",
  "用户确认后能在可见浏览器中完成受控动作",
  "每个目标都有结构化结果，成功必须由页面最终状态或平台可核对结果证明",
  "用户可以暂停、恢复、取消和紧急停止",
  "遇到验证码、风控、登录失效或未知页面时不误点、不自动绕过，进入人工处理",
  "App、Control Plane 或 Local Executor 崩溃重启后，不重复已经确认完成的副作用，并能恢复任务快照",
  "结果不确定的动作不会自动重试",
  "日志、事件和截图不泄漏 Cookie、Token、完整页面私密内容或本机私有路径",
  "macOS 和 Windows 分别完成安装、启动、外部浏览器、Local Executor 生命周期和真实测试账号验收",
  "正式安装包不包含测试驱动、调试端口、测试凭据或真实运行数据",
];

const REQUIRED_FINDINGS = [
  "H8-16A",
  "H8-16B",
  "H8-16C",
  "H8-16D",
  "H8-16E",
  "H8-16F",
];

test("H8-16 keeps the P9 MVP specification review executable and evidence-bound", async () => {
  const [reviewText, productPlan, roadmap] = await Promise.all([
    readFile(reviewUrl, "utf8"),
    readFile(new URL("docs/product-plan.md", repositoryRoot), "utf8"),
    readFile(new URL("docs/development-roadmap.md", repositoryRoot), "utf8"),
  ]);
  const review = JSON.parse(reviewText);
  assert.equal(review.version, "mvp.spec-review.v1");
  assert.equal(review.scope, "p9.local-single-device-mvp");
  assert.deepEqual(
    review.acceptanceCriteria.map(({ criterion }) => criterion),
    ACCEPTANCE_FRAGMENTS,
  );
  assert.deepEqual(
    review.findings.map(({ remediationTask }) => remediationTask),
    REQUIRED_FINDINGS,
  );

  const acceptanceSection = productPlan.split("### 4.5 MVP 验收标准")[1]?.split("## 5.")[0];
  assert.ok(acceptanceSection, "product-plan MVP acceptance section is missing");
  for (const [index, item] of review.acceptanceCriteria.entries()) {
    assert.equal(item.id, `MVP-AC-${String(index + 1).padStart(2, "0")}`);
    assert.ok(acceptanceSection.includes(item.criterion));
    assert.ok(
      [
        "automated_complete",
        "pending_real_platform",
        "pending_packaging",
        "remediation_required",
      ].includes(item.disposition),
    );
    assert.ok(item.evidence.length > 0);
  }

  for (const decision of review.architectureDecisions) {
    assert.match(decision.id, /^MVP-ADR-\d{2}$/u);
    assert.equal(decision.disposition, "conforms");
    assert.ok(decision.evidence.length > 0);
  }

  for (const finding of review.findings) {
    assert.ok(["open", "resolved"].includes(finding.state));
    assert.ok(roadmap.includes(`| ${finding.remediationTask} |`));
    assert.ok(finding.requirementRefs.length > 0);
    assert.ok(finding.evidence.length > 0);
    if (finding.state === "resolved") {
      assert.ok(finding.resolutionEvidence?.length > 0);
      assert.ok(
        roadmap
          .split("\n")
          .find((line) => line.startsWith(`| ${finding.remediationTask} |`))
          ?.includes("✅ 已完成"),
      );
    }
  }

  const allEvidence = [
    ...review.architectureDecisions.flatMap(({ evidence }) => evidence),
    ...review.acceptanceCriteria.flatMap(({ evidence }) => evidence),
    ...review.findings.flatMap(({ evidence }) => evidence),
    ...review.findings.flatMap(({ resolutionEvidence = [] }) => resolutionEvidence),
  ];
  for (const evidence of allEvidence) {
    assert.match(evidence.path, /^(README\.md|docs|backend|frontend)\//u);
    assert.ok(evidence.anchor.length >= 6);
    const source = await readFile(new URL(evidence.path, repositoryRoot), "utf8");
    assert.ok(source.includes(evidence.anchor), `${evidence.path} evidence anchor drifted`);
  }
});
