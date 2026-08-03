# 后端覆盖率债务 —— 交接（2026-08-03 晚）

用户可操作：否
证据类型：文档

> 分支：`coverage/backend-100`（worktree `wt/coverage-100`，基于 `origin/main@5c1387d9`）
> 上游计划：[2026-08-03-backend-coverage-debt-plan.md](2026-08-03-backend-coverage-debt-plan.md)
> **这个分支不许合回 `main`，直到全库 100%。** 门禁 `fail_under = 100` 是全库的，
> 中途合进去只会让 `main` 的 backend job 继续红。

## 1. 现在到哪了

| 任务 | 起点 | 现在 | 状态 |
|---|---:|---:|---|
| COV-00 基线与排除项审计 | — | — | ✅ |
| COV-01 对齐已有测试收集边界 | — | — | ✅ |
| COV-02 本地/智能剪辑 | 768 | **0** | ✅ 已由含 integration 的全量验证 |
| COV-03 动效作者链 | 388 | **43** | 🚧 十一个模块已收口，见 [COV-03](COV-03.md) |
| COV-04 语音/音频 | 199 | 199 | 未开始 |
| COV-05 发布链 | 178 | 178 | 未开始 |
| COV-06 平台尾项与最终审计 | 129 | 129 | 未开始 |

全库缺口从 **2,023** 降到 **551**（COV-01 后为 1,664，减 COV-02 的 768 与
COV-03 已消除的 345）。

## 2. 接手第一件事：重新测一次

不要相信这份文档里的数字，先自己测：

```bash
cd wt/coverage-100/backend
uv run --frozen pytest tests -q --cov=automation_tool \
  --cov-report=json:/tmp/now.json --cov-fail-under=0
```

含 integration 约 **10 分钟**（它按测试起独立的 Docker Compose PostgreSQL 实例）。
只要不碰仓储和控制面，`tests/unit` 就够，约 **90 秒**。

读缺口：

```python
python3 -c "
import json
d=json.load(open('/tmp/now.json'))
rows=[(len(v['missing_lines'])+len(v.get('missing_branches',[])), f) for f,v in d['files'].items()]
for gap,name in sorted((r for r in rows if r[0]), reverse=True)[:30]:
    print(f'{gap:5d}  {name}')
print('TOTAL', sum(g for g,_ in rows))
"
```

## 3. 这一轮反复踩到的事（照着做能省很多时间）

### 3.1 「断言通过但被测路径没执行」出现了五次

五次里四次是本轮新写的测试，一次是既有测试。**只看 pytest 是绿是红一次也发现不了**，
全靠缺口数字没降才暴露。所以：

**每写完一批就用集合运算量一次，别只看测试通过。**

具体形状：

- 用 monkeypatch 把某个常量调小来触发上限，而那个常量在更早的地方也被用到，于是
  运行在更早那关就死了；
- fixture 里用了 catalog 中不存在的 `model_id`，于是加载本来就失败，mock 的那个
  特定失败从未被触及；
- **patch `os.name="nt"` 之后构造的 `Path` 是 `WindowsPath`**，`lstat` 与路径比较在
  macOS 上必然失败，被测函数在最开头就返回了。这个踩了三次。路径对象要在 patch 外
  构造；
- 既有测试没传某个必填字段，而那个字段的校验排在被测规则之前。用给 `_reject` 打点
  的办法定位到具体行号才确认；
- 契约「漂移」用例只删掉一个键，而实现是 `contract["key"]` 直接取值——`KeyError` 被
  更上面的 `except` 接住走了「读不出」那条，于是漂移用例和「文件不存在」验的是同一
  条路。**要到达下一道检查，样本必须让上一道过得去。**

### 3.2 不可达分支：改断言，不加 pragma

本轮遇到六处 `if` 的某一侧在任何输入下都到不了。**全部改成 `assert`**，判据一致：

- 断言行每次都执行，因而可覆盖；
- 语义仍是防御；
- 规则一放宽就是响亮失败，而不是静默走偏。

六处在 `adaptive_frame_extraction`、`local_material_preview`、`smart_edit_pipeline`、
`timeline_repository`、`material_repository`、`part_typography`。

**全程没有新增 `pragma: no cover`、没有新增 `omit`、没有降低 `fail_under`。** 接手
的人也别加——那等于把债务换个地方藏起来。

### 3.3 几个具体的坑

- **helper 里有 `mkdir` 就不能在循环里调**，第二轮 `FileExistsError`。踩了三次；
- **fd 号会被复用**：关掉一个描述符之后，紧接着打开的文件常常拿到同一个号。「只对
  这个 fd 生效」的 patch 会误伤下一个使用者；
- **`Path.resolve()` 跟随符号链接**，所以「这个路径是不是链接」的检查通常守的是
  TOCTOU 竞态，而不是「本来就是链接」。要制造那个窗口只能挂钩子；
- **符号链接循环**在 3.12 的 `resolve()` 上抛 `RuntimeError`，不是 `OSError`；
- **域模型自己会拦**，所以「下游不信任传进来的东西、自己再验一遍」这件事，用工厂
  造出来的样本证明不了。需要绕过构造函数组装样本（`__new__` + `object.__setattr__`）；
- **有些子条件从存在的命令构造不出来**（例如写回声明用户归属时既不能带模型标签也
  不能带时间戳）。**不要为了凑覆盖去放宽域模型**，注明原因即可。

## 4. 下一步怎么排

建议顺序：**COV-03 收尾 → COV-04 → COV-05 → COV-06**。理由是 COV-03 已经开了头，
上下文还热。

### 4.1 COV-03 剩 43 点

见 [COV-03.md](COV-03.md) §4。三块：

1. **`agent.py` 剩 27 点**——打包契约加载与边界类型闸已做完，剩下的是作者主循环
   里的分支：修复轮改动过大、品牌素材元素校验、片段时刻解析、模型输出不是 JSON
   对象、槽位溢出探针测量失败、简报类型闸、静态门禁失败。另有一条传输层用例被我
   删掉了（写坏了，见 COV-03 §4.3），需要重写；
2. **`entry.py` 剩 14 点**——契约加载与漂移要 patch `_REFUSAL_CONTRACT_PATH`；旁白
   闭包要真实配音配置；`serve_one_motion_authoring_request` 的成功路径它不接受
   `model_call`，会真的调模型，得另想办法（现有的子进程测试是另一条路）；
3. **`part_workspace.py` 剩 2 点**——元素开标签不以 `>` 结尾。我没找到能让
   `HTMLParser` 产出这种 span 的文档，怀疑也是不可达，但**没确认，别直接当不可达
   处理**。

### 4.2 COV-04 / 05 / 06

计划文档 §5.3～§5.5 有逐模块表。COV-06 里 `windows_candidate.py` 那一批可以用本轮
验证过的「平台值注入」手法（COV-02 §2.2、COV-03 §2.2）。

## 5. 收口时要做的事

1. 两次连续的全量运行都到 100%（COV-00 §2.2 记录过一个竞态分支会漂，虽然本轮已经
   把它钉死，但这条判据保留）；
2. 重新审计那 49 个 `# pragma: no cover`（COV-00 §3 有逐条结论，收口时再过一遍）；
3. `ruff` / `ruff format` / `mypy` 全绿；
4. `python3 scripts/check_acceptance_evidence_depth.py` 退出 0；
5. 合回 `main` 用 `git merge --no-ff`。

## 6. 提交记录

本轮在 `coverage/backend-100` 上的提交都带 `test(cov-0N):` 前缀，每条提交信息里
记了当批的判断依据和踩到的坑——那些是比代码更难重建的部分，接手前值得扫一遍
`git log --oneline origin/main..coverage/backend-100`。
