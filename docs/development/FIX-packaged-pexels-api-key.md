# FIX：Pexels 素材密钥随包内置，用户不再手工填写

用户可操作：否

证据类型：分层实现

> 日期：2026-08-05
>
> 提交：本文件所在提交
>
> 类型：独立缺陷修复（不改任何 roadmap 任务状态）

## 缺陷

用户在正式包里点「生成视频」，被要求先填 Pexels API key——而这台机器上密钥早就
存在（7/26 已放入凭据目录），且用户当时就说过要内置。查证结果：**产品从未把素材
站点密钥接进上游 WebUI**。自有后端 grep `pexels` 零命中；上游读的是自己
`config.toml` 里的 `pexels_api_keys = []`，而私有配置注入机制
（`_preload_private_config`）只注入了中文字幕字体，从没人给过这个字段。

## 设计

密钥走三段既有通道，不新开一条：

1. **编译期**：`build_release_package.py --pexels-api-key <path>` 读操作员的 0600
   私有文件（形状先验：20–120 位字母数字），经 `release_environment()` 进入
   cargo 编译环境 `AUTOMATION_TOOL_PEXELS_API_KEY`——与部署 Profile 同一姿势，
   **argv 里只有路径，仓库里永远没有值**；
2. **进程间**：Rust 引导文档新增 `pexelsApiKey` 字段（`option_env!` 烘进二进制，
   无密钥的构建发 null）。字段**始终在场**——两个 worker 读者都做精确形状校验，
   可选字段会让「带密钥的包」和「不带的包」变成两种协议；
3. **worker 内**：材料网关校验后传给 `start_webui` → 子进程 stdin 引导行 →
   `_private_config_document` 把 `pexels_api_keys = ["…"]` 插进上游 `[app]` 段——
   与字幕字体同一机制，上游其余内容逐字保留。密钥只落在 0o700 私有运行时目录。

## RED（每层先红）

```text
scripts/test_material_video_worker.py（3 条新用例）
  GatewayRejected: invalid bootstrap        ← 引导不认识 pexelsApiKey
  TypeError: _private_config_document() got an unexpected keyword 'pexels_api_key'
scripts/test_build_release_package.py（2 条新用例）
  AttributeError: module has no attribute 'read_pexels_api_key'
cargo（2 条新用例）先于实现编写，随实现一并落地
```

## GREEN

```text
scripts/test_material_video_worker.py         76 passed, 1 skipped
scripts/test_build_release_package.py         Ran 16 tests, OK（原 14 + 2）
backend tests/unit/executor 全量               3907 passed, 2 skipped
cargo test --lib pexels                        2 passed（两条名字逐条出现）
scripts/check_release_package_wiring.py        通过
scripts/check_script_import_symbols.py         通过
ruff：改动前后报错逐类对比零差异（全部既有）
```

## 过程中抓到并当场修正的三个问题

1. **两读者白名单不一致**：材料网关先做成可选、执行器侧精确匹配，加键后不带字段的
   文档反被执行器拒（退出码 65）。统一为「字段必须在场、可为 null」；
2. **批量给测试夹具补字段时脚本插错**：命令文档（带 `materialId`/`jobId`）不是引导
   文档，甚至插进了字符串集合字面量。按 §8.4 逐 diff 核对后回滚三个文件改为手工，
   最终只有两处真正的引导文档需要补；
3. **新测试类写在 `unittest.main()` 之后跑了零次**——计数仍是 14 且 OK。主入口移到
   文件末尾后 16 条才真实执行（T51/T62 同族问题）。

## 真实边界

- **密钥在客户端二进制里可被提取**（`strings` 即可），已向用户说明：Pexels 免费
  key 可随时在其后台轮换，轮换后重出包即可。这是用户知情后的明确决定；
- 上游 WebUI 设置页会显示注入的 key——上游行为，V3 React 重写后该界面整体消失；
- 本轮证据到单元/契约层为止：**尚未在真实包上跑「不填 key 直接生成视频」**，该验收
  需要下一次出包（本次改动已使当前公证包的源码摘要过期），归入 V3 完成后的整链验收；
- 旧策略测试 `test_no_font_binary_is_checked_into_the_repository` 钉的「字体不入库」
  已被用户 2026-08-05 明确反转，改写为「每个已清权字体必须以契约摘要入库」。

## 清理

无临时产物；三个被误改的测试文件已回滚并手工重做。

## 遗留项

| 项 | 状态 |
| --- | --- |
| 真实包上验证不填 key 出片 | 待下次出包（与 V3 整链验收合并） |
