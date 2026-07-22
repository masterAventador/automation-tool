# Customer Demo runtime secrets

`inventory.v1.json` 是 C10-05 的唯一 Secret inventory。生产 Control Plane 镜像固定 `AUTOMATION_TOOL_RUNTIME_SECRET_MODE=files`，只读取 `/run/secrets` 下 inventory 指定的五个固定文件名；Secret path 不是部署输入，不能通过环境变量或 argv 改写。

Secret Store 投影文件必须是 runtime UID `65532` 自有的 `0400` 普通文件，或 root 自有、runtime group 可读的 `0440` 普通文件。装载器拒绝 symlink、目录、其他 owner、owner/group/other 可写、other 可读、可执行、超过 8192 bytes、空值、非 UTF-8、首尾空白、换行和 NUL。Secret volume 只读挂载；值只在进程启动时读取，轮换必须按 inventory 顺序完成并受控重启对应 consumer。

账号 access/refresh Session 与设备 credential 都是 CSPRNG opaque secret，服务端只保存 digest，不存在第二份 Session 签名 Secret 或设备 credential 签发私钥。设备 Ed25519 私钥由 App 私有存储持有；Control Plane 只接收公钥。旧 bootstrap issuer 私钥留在离线运维边界，服务端只有公开 verification key。持久服务端私钥只有动作授权 Ed25519 key。

本目录不保存 Secret 值、示例值、生成结果或可直接部署的 Secret manifest。真实云 Secret Store、访问策略与注入由获得用户明确授权后的 C10-08 执行。
