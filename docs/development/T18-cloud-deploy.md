# T18-cloud-deploy 真实云主机 Demo 部署

- 状态：✅ 已完成
- 日期：2026-07-26
- 提交：`d1f4650`（部署链路）、`82b3b4d`（重复部署缺陷修复）
- 交付镜像：`automation-tool-demo-control-plane:82b3b4db2b82`，
  `org.opencontainers.image.revision=82b3b4db2b82a33efc14c5f4ad5770dcd58be259`（等于 `82b3b4d`）
- 范围：把 Python Control Plane 与 PostgreSQL 真实部署到客户云主机 `49.233.213.109`，
  经主机既有 nginx 以 `https://at.xuanbai.tech` 对外提供产品 API；部署过程沉淀为仓库内可重复脚本；
  从本机走公网 HTTPS 完成产品账号登录与认证业务接口的纵向验收
- 前置依赖：C10-01～C10-13（provider-neutral 部署契约、镜像、Secret、账号运维、App Demo Profile 均已完成）

## RED

先写 `deploy/cloud/test_cloud_deployment.py`，再写任何部署工件：

```
$ python3 -m unittest discover --start-directory deploy/cloud --top-level-directory deploy/cloud
ImportError: Failed to import test module: test_cloud_deployment
ModuleNotFoundError: No module named 'deploy_cloud_demo'
Ran 1 test ... FAILED (errors=1)
```

依赖下载主机常量是第二轮 RED（先测后改 `backend/Dockerfile`）：

```
FAIL: PackageDownloadHost.test_the_build_rewrites_only_the_download_host
AssertionError: 'sed -i "s|https://files\.pythonhosted\.org/|https://pypi.tuna.tsinghua.edu.cn/|g" uv.lock' not found
Ran 36 tests ... FAILED (failures=1)
```

## GREEN

契约测试 45/45 通过，`ruff check` 通过；既有 `control-plane-container-boundary`、
`runtime-secret-delivery`、`https-ingress-boundary`、`customer-demo-*` 共 15 条回归通过。

从**本机**走公网 HTTPS 对 `https://at.xuanbai.tech` 的真实验收（`deploy/cloud/verify_cloud_demo.py`）：

```
[verify] https://at.xuanbai.tech
  ok  public HTTPS health returns 200 (got 200)
  ok  health projection has the fixed contract shape
  ok  edge sets strict-transport-security / x-content-type-options
  ok  edge sets content-security-policy / referrer-policy
  ok  public HTTPS version returns 200 (got 200)
  ok  version reports the Control Plane service / API v1
  ok  unauthenticated business read is rejected (got 401)
  ok  wrong password is rejected (got 401)
  ok  product account login returns 201 (got 201)
  ok  login returns the same account / the Demo account is active
  ok  authenticated business read returns 200 (got 200)
  ok  device list projection is present
  ok  a tampered access token is rejected (got 401)
  ok  refresh rotates the session (got 201) / refresh token rotates on use
  ok  a replayed refresh token is rejected (got 401)
  ok  logout closes the rotated session (got 401)
  ok  plain HTTP is redirected to HTTPS (got 308)
  ok  the redirect target is the public HTTPS host
[verify] every public acceptance assertion passed
```

链路为：真实 DNS → 公开 Let's Encrypt 证书 → 主机 nginx 边界 → `127.0.0.1:18800` →
Control Plane 容器 → PostgreSQL 容器。产品账号 `xuanbai.demo` 登录取得 Session，
再用该 Session 读 `/api/v1/account-installations` 取得业务投影。

部署产物清单：

| 项 | 值 |
| --- | --- |
| 宿主端口 | `127.0.0.1:18800`（唯一；PostgreSQL 不在任何宿主接口监听） |
| 容器 | `automation-tool-demo-control-plane-1`、`automation-tool-demo-postgres-1` |
| 网络 | `automation-tool-demo-net` |
| 卷 | `automation-tool-demo-{postgres-data,postgres-secrets,runtime-secrets,migration-secrets}` |
| 镜像大小 | 522 MB（磁盘）/ 126 MB（内容） |
| 内存实测 | Control Plane 69.5 MiB / 1 GiB，PostgreSQL 35.1 MiB / 640 MiB |
| 宿主内存余量 | 2674 MiB available（总 3729 MiB） |
| Alembic revision | `20260723_0034` |

## 关键决策

### 1. 边界层是主机 nginx，不是 `deploy/ingress` 容器

`deploy/customer-demo/compose.v1.json` 假设 ingress 容器独占公网 443。这台主机 443 已被用户
其它业务（af / agentdemo）的 nginx 占用，且 `deploy/ingress/render_config.py` 的域名校验只接受
`api.<domain>`，`at.xuanbai.tech` 过不了。更关键的是：两层 nginx 串联会让内层的
`$binary_remote_addr` 恒为 `127.0.0.1`，把「按来源限流」退化成所有客户端共用一个桶——这是
实打实的功能缺陷，不是风格问题。

因此主机 nginx 作为唯一边界，ingress 模板中的请求上限、超时、限流、安全响应头逐条搬进
`deploy/cloud/nginx-site.conf.template`。产品代码路径完全相同：同一个 `backend/Dockerfile`、
同一份 Alembic 迁移、同一套 `/run/secrets` 文件式密钥投递；差异只在边界层与配置值。

### 2. 依赖下载主机改为路径一致的完整镜像，且是单一构建路径

本主机到 `files.pythonhosted.org` 只有 21,931 B/s，一次构建 40 分钟以上。清华镜像的
`/packages/<a>/<b>/<digest>/<file>` 路径与官方逐字相同，只替换主机名即可命中同一文件：

| 来源 | 吞吐 |
| --- | --- |
| `files.pythonhosted.org` | 21,931 B/s（25 秒 548 KB 未下完） |
| `pypi.tuna.tsinghua.edu.cn` | 54,365,470 B/s（4.7 MB / 0.087 秒） |

实测下载产物 sha256 `0e959b57…f325` 与 `uv.lock` 记录完全一致；一次性 probe 镜像验证
`uv sync --locked` 接受改过 host 的 lock，59.4 秒装完 65 个包（probe 已清理）。

锁定强度未变：`--locked` 仍在，每个产物仍按 lock 的 sha256 校验；换的是字节从哪台服务器来，
锁的是装哪个版本、内容必须是什么。主机名是 Dockerfile 常量而非 build-arg / 环境变量 /
条件分支——CI、开发机、云主机走同一条路径，避免「测试构建与出厂构建依赖解析方式不同」这一
本仓库出过事故的形态。仓库内 `uv.lock` 未被修改，只改镜像内副本。

前一轮曾误判「换源必然破 `--locked`」，原因是当时改的是 `UV_DEFAULT_INDEX`（改了 registry
本身）而非只改 wheel 下载 host，两者不等价，未分开验证即下结论。

## 真实边界

- 验收全程从本机发起，不在服务器内部自证；走真实 DNS、公开证书与主机 nginx，
  与 Tauri App 将来走的是同一条链路；
- 验收所用的镜像就是当前正在服务的那一个，其 OCI revision 等于本任务最后一个提交；
- 未做：Tauri App 端到端（属另一条工作线的正式包任务）、真实平台账号 RPA 动作、
  宿主整机重启（会中断用户其它生产业务，改用 `systemctl restart docker` 等价验证容器恢复）。

## 失败矩阵

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 未认证读业务接口 | 拒绝 | 公网 HTTPS `GET /api/v1/account-installations` → 401 |
| 错误口令登录 | 拒绝 | 401，无账号存在性泄漏差异 |
| 篡改 access token | 拒绝 | 401 |
| refresh token 重放 | 拒绝并吊销整族 | 首次 refresh 201 且轮换，重放 401，随后注销亦 401 |
| 明文 HTTP 访问 | 308 跳转 HTTPS | `Location: https://at.xuanbai.tech/...` |
| 重复部署（幂等） | 通过 | 第二次 31 秒完成，复用全部密钥/数据卷/账号，`reused the existing Demo account` |
| 迁移前备份 | 通过 | `verified backup automation_tool_demo-20260726T051308Z.dump (164375 bytes)` + receipt |
| nginx 配置非法 | 自动回滚且不 reload | 注入非法指令 → `rollback valid=True`，磁盘配置回滚，`nginx -t` 仍成功 |
| Docker 守护进程重启 | 容器自动恢复 | `restart=unless-stopped` + `systemctl is-enabled docker=enabled`；重启后两容器 healthy，重跑公网验收全通过 |
| 端口占用 | 事前规避 | 部署前核对 18800/18801/18802 均空闲，未接管任何来源不明进程 |
| 密钥泄漏 | 未发现 | 容器日志 grep `password|pepper|secret=` 计数为 0；`/run/secrets` 全为 `0400 automation-tool` |
| 影响用户其它业务 | 未发生 | af / agentdemo 在部署后与回滚测试后均 HTTP 200；nginx 主进程 PID 未变（reload 非 restart） |

## 清理

- 删除本次产生的过期镜像 `…:a0a61e585262`、`…:d1f465096282`，只保留交付中的 `…:82b3b4db2b82`；
- 删除一次性 probe 镜像 `uvprobe` 与 `/tmp/uvprobe`；
- 服务器上 AppleDouble 残留清零（`find -name "._*" | wc -l` = 0）；
- 未触碰任何非本项目的容器、网络、卷、镜像或 nginx 站点；未执行 `docker system prune`。

## 遗留项

- Control Plane 镜像目前多背约 50 MB：`backend/pyproject.toml` 把 `playwright==1.61.0` 声明为
  主依赖，`uv sync --no-dev` 会装进镜像。`control_plane/` 代码无任何 playwright import（仅
  `executor/` 使用），CLAUDE.md 4.2 的边界在代码层守住、在打包层破了。修法为拆
  optional-dependency group，已由协调方立为独立待办 T45，需 `uv.lock` 无并行占用时进行，
  本任务不动。
- **Demo 账号凭据不在 Git 内**，只存于服务器 `/etc/automation-tool-demo/secrets.json`（0600，root-only），
  交付时口头/交付说明单独给出。登录名 `xuanbai.demo`，userId `5c032309-58ed-435a-ba59-d23228b462d1`。
- **App 端尚未接入**：本任务只证明云端产品 API 可用。Tauri 正式包连 `https://at.xuanbai.tech`
  的纵向验收属另一条工作线，两边都通过后客户 Demo 才算可交付。
- **设备注册 bootstrap 私钥**：`bootstrapPrivateSeed` 存在服务器密钥文件中，公钥已注入 Control Plane。
  若 App 的设备注册链路需要该私钥签发 bootstrap token，需与正式包工作线对齐分发方式；
  U9-05 的"登录后自动绑定设备"路径不依赖它，本次未验证需要它的分支。
- **证书续期**：`at.xuanbai.tech` 证书 2026-10-24 到期，由主机既有 certbot（nginx authenticator）
  自动续期，本部署复用同一 lineage，未新增 deploy hook。续期后 nginx 由 certbot 自行 reload。
- **备份未自动轮转**：`/var/backups/automation-tool-demo/` 每次部署新增一份 dump，
  当前无保留期上限，长期运行需补清理策略。
