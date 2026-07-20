# Executor v1 fixtures

本目录是 Python、Rust 和 TypeScript 共同回放的协议事实源。`valid/` 中 10 个样例必须同时通过 Draft 2020-12 JSON Schema 和正式语义解析器；`invalid/` 中 27 个样例必须由正式解析器统一拒绝。

JSON Schema 能表达字段、判别枚举、required、unknown field、UUID/幂等键 pattern、序号和 UTC RFC3339 pattern。以下 10 个语义层无效样例需要语言适配器显式实现 Schema 扩展 `x-semantic-validation-required`，不能因为标准 Schema 接受就放行：

- `deadline-before-send.json`
- `deadline-before-send-microsecond.json`
- `deadline-equals-send.json`
- `duplicate-key.json`
- `inline-data-uri.json`
- `non-finite-number.json`
- `payload-too-deep.json`
- `private-path.json`
- `sensitive-assignment.json`
- `sensitive-cookie-field.json`

其余 17 个结构层无效样例必须被标准 Draft 2020-12 validator 和正式解析器同时拒绝。Fixture 不含真实 Cookie、Token、私钥、账号、路径或用户数据；隐私拒绝用例中的字段和值都只是显式 `fixture` 测试标记。

`duplicate-key.json` 必须以原始 UTF-8 文本读取，不能先经会吞掉重复 key 的普通 JSON object loader；`non-finite-number.json` 故意携带 JSON 标准之外的 `NaN`，用于证明每种语言都 fail closed。其余 fixture 都是普通 JSON document。
