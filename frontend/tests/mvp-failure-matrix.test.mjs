import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);
const registryUrl = new URL("contracts/quality/mvp-failure-matrix.v1.json", repositoryRoot);

const EXPECTED = new Map([
  ["安装实例", ["未注册", "过期", "吊销", "重放", "跨环境", "冒充"]],
  ["Control Plane", ["未启动", "版本不兼容", "数据库不可用", "重启", "超时"]],
  ["PostgreSQL", ["唯一冲突", "revision CAS", "事务回滚", "迁移失败", "连接耗尽"]],
  ["Executor", ["未安装", "签名/版本错误", "启动超时", "挂起", "崩溃循环"]],
  ["App ↔ Executor", ["token 错误", "协议漂移", "stdout 破损", "stop/invoke 竞争"]],
  ["Executor ↔ Server", ["断网", "乱序", "重复", "迟到事件", "心跳丢失", "旧连接"]],
  ["浏览器", ["未安装", "路径失效", "Profile 锁", "版本升级", "窗口关闭", "进程残留"]],
  ["平台登录", ["未登录", "二维码过期", "登录过期", "验证码", "风控", "权限差异"]],
  ["目标发现", ["空结果", "重复", "无限滚动", "弹窗", "页面改版", "目标变化"]],
  ["外部动作", ["超频", "取消竞态", "发送后断网", "验证失败", "重复执行"]],
  ["任务控制", ["暂停/完成", "取消/完成", "紧停/dispatch", "重复控制"]],
  ["恢复", ["App 崩溃", "Executor 崩溃", "Control Plane 崩溃", "机器休眠"]],
  ["Artifact", ["磁盘满", "过大", "过多", "权限拒绝", "路径替换", "清理失败"]],
  ["隐私", ["Cookie", "Token", "页面", "聊天", "绝对路径"]],
  ["安装包", ["测试驱动", "调试权限", "真实数据", "错误平台二进制", "未签名文件"]],
]);

test("H8-15 keeps every MVP failure branch bound to durable evidence", async () => {
  const [registryText, roadmap] = await Promise.all([
    readFile(registryUrl, "utf8"),
    readFile(new URL("docs/development-roadmap.md", repositoryRoot), "utf8"),
  ]);
  const registry = JSON.parse(registryText);
  assert.equal(registry.version, "mvp.failure-matrix.v1");
  assert.deepEqual(
    registry.realPlatformEvidencePending,
    ["B5-15", "D6-16", "A7-16", "A7-17"],
  );
  assert.equal(registry.boundaries.length, EXPECTED.size);

  const seenBoundaries = new Set();
  for (const boundary of registry.boundaries) {
    assert.ok(EXPECTED.has(boundary.name), `unexpected boundary: ${boundary.name}`);
    assert.equal(seenBoundaries.has(boundary.name), false, `duplicate boundary: ${boundary.name}`);
    seenBoundaries.add(boundary.name);
    const expectedCases = EXPECTED.get(boundary.name);
    assert.deepEqual(boundary.cases.map(({ name }) => name), expectedCases);
    const roadmapRow = roadmap
      .split("\n")
      .find((line) => line.startsWith(`| ${boundary.name} |`));
    assert.ok(roadmapRow, `${boundary.name} is missing from roadmap section 4.1`);

    for (const failure of boundary.cases) {
      const roadmapTerm = failure.name.endsWith(" 崩溃")
        ? failure.name.slice(0, -" 崩溃".length)
        : failure.name;
      assert.ok(roadmapRow.includes(roadmapTerm), `${boundary.name}/${failure.name} left roadmap`);
      assert.equal(failure.disposition, "automated");
      assert.match(failure.evidence.path, /^(backend|frontend)\//u);
      assert.ok(failure.evidence.anchor.length >= 12);
      const evidence = await readFile(new URL(failure.evidence.path, repositoryRoot), "utf8");
      assert.ok(
        evidence.includes(failure.evidence.anchor),
        `${boundary.name}/${failure.name} evidence anchor drifted`,
      );
    }
  }
  assert.deepEqual([...seenBoundaries], [...EXPECTED.keys()]);
});
