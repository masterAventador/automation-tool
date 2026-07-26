# 云端客户 Demo 部署（`at.xuanbai.tech`）

本目录是**真实云主机**上客户 Demo Control Plane 的可重复部署工件。`deploy/customer-demo/`
是 provider-neutral 的发布契约与隔离演练；本目录是它在这台具体主机上的落地实现。

## 1. 目标主机与拓扑

```text
公网 HTTPS  ──▶  主机 nginx 1.26.3（已存在，同时服务 af / agentdemo 等其它业务）
                     │  仅新增一个 at.xuanbai.tech server 块
                     ▼
              127.0.0.1:18800  ──▶  control-plane 容器（8000，仅 loopback 发布）
                                         │  automation-tool-demo-net（私有 Docker 网络）
                                         ▼
                                    postgres 容器（5432，不发布任何宿主端口）
```

* 主机：`49.233.213.109`，Debian 13，4 核 / 3.6 GiB 内存 / 32 GiB 可用磁盘。
* 证书：复用主机上已签发的 `/etc/letsencrypt/live/at.xuanbai.tech/`，由既有 certbot 续期。
* 本部署**只新增**资源；不修改、不重启、不删除主机上任何既有站点、证书、容器或服务。

### 与 `deploy/customer-demo/compose.v1.json` 的差异（必须知道）

该清单假设 **ingress 容器是唯一公网入口**并自行终止 TLS。这台主机上 80/443 已由用户其它
业务的 nginx 占用，因此：

1. **边界层是主机 nginx，不是 `deploy/ingress` 容器。** 该 ingress 镜像的
   `render_config.py` 只接受 `api.<domain>` 形式的域名，`at.xuanbai.tech` 无法通过校验；
   即使能通过，两层 nginx 串联也会让内层的 `$binary_remote_addr` 恒为 `127.0.0.1`，
   把「按来源限流」退化成「所有客户端共用一个桶」。所以 ingress 容器在本拓扑中不使用，
   其请求上限、超时、限流与安全响应头**逐条搬到** `nginx-site.conf.template`。
2. **Control Plane 发布一个 loopback 端口**（`127.0.0.1:18800`），供主机 nginx 反代；
   清单里的「应用容器不发布任何宿主端口」在单机同主机边界下由 loopback 绑定等价保证。

产品代码路径完全相同：同一个 `backend/Dockerfile`、同一份 Alembic 迁移、同一套
`/run/secrets` 文件式密钥投递。差异只在边界层与配置值。

## 2. 资源清单（全部带 `automation-tool-demo` 前缀）

| 类型 | 名称 |
| --- | --- |
| Compose project | `automation-tool-demo` |
| 网络 | `automation-tool-demo-net` |
| 卷 | `automation-tool-demo-postgres-data` |
| 卷 | `automation-tool-demo-postgres-secrets` |
| 卷 | `automation-tool-demo-runtime-secrets` |
| 卷 | `automation-tool-demo-migration-secrets` |
| 镜像 | `automation-tool-demo-control-plane:<commit 前 12 位>` |
| 宿主端口 | `127.0.0.1:18800`（仅此一个） |
| 主机状态目录 | `/etc/automation-tool-demo/`（0700，root-only） |
| 备份目录 | `/var/backups/automation-tool-demo/`（0700） |
| nginx 站点 | `/etc/nginx/sites-available/automation-tool-demo.conf` |

非密钥参数集中在 `demo-environment.json`，nginx 站点与 Compose 变量都由它派生，不存在第二处硬编码。

## 3. 部署

```bash
# 从本机（会把工作树的可部署子集打包送上去，然后在服务器上执行部署）
deploy/cloud/deploy.sh                      # 默认 root@49.233.213.109
DEPLOY_EXTRA_ARGS=--skip-build deploy/cloud/deploy.sh   # 镜像已在服务器上时跳过构建
```

脚本是**幂等**的：已持久化的密钥、PostgreSQL 数据卷和已创建的 Demo 账号都会被复用，
重复执行只会替换镜像、跑迁移、重启 Control Plane 并重装 nginx 站点。

串行阶段：构建镜像 → 校验 OCI 身份 → 建网络/卷 → 生成或复用密钥 → 写入只读密钥卷 →
启动 PostgreSQL → 建角色/库/权限 → 迁移前备份 → 一次性 Alembic 迁移 → 启动 Control Plane →
安装 nginx server 块（先 `nginx -t`，失败自动回滚，再 `systemctl reload`）→ loopback 健康校验 →
确保 Demo 账号存在。

### 镜像构建位置

在服务器上用 `backend/Dockerfile` 构建。若需在别处构建，必须使用同一个 Dockerfile 与
同样的 `APP_VERSION` / `VCS_REF` build-arg，然后 `docker save | ssh <host> docker load`，
再用 `--skip-build` 部署——部署器会校验镜像的 `org.opencontainers.image.version` /
`.revision` 标签，不匹配直接失败。

### 为什么依赖下载走镜像源，以及为什么这不削弱依赖锁定

**不要把 `backend/Dockerfile` 里那行 `sed` 改回去。** 它不是安全妥协，删掉它会让这台主机上
的构建从 1 分钟退回 40 分钟以上。

实测（本主机，`uv.lock` 里的真实 wheel `cryptography-49.0.0-...manylinux2014_x86_64.whl`）：

| 来源 | 吞吐 |
| --- | --- |
| `files.pythonhosted.org` | 21,931 B/s（25 秒只下了 548 KB，未下完） |
| `pypi.tuna.tsinghua.edu.cn` | 54,365,470 B/s（4.7 MB 整包 0.087 秒） |

清华镜像是**完整镜像**，`/packages/<a>/<b>/<digest>/<file>` 路径与官方逐字相同，所以只替换
主机名，`uv.lock` 里每一条被锁定的 URL 都仍然命中同一个文件。实测下载后 sha256 与 lock 中
记录的 `0e959b57…f325` 完全一致。

锁定强度没有任何变化：

* `--locked` 仍在，uv 不会解析任何 lock 未固定的东西；
* 每个产物仍按 `uv.lock` 里的 sha256 校验，镜像若返回不同内容会被直接拒绝；
* 换的是"这些字节从哪台服务器来"，锁的是"装哪个版本、内容必须是什么"——是两件事。

**这是单一构建路径。** 主机名是 Dockerfile 里的常量，不是 build-arg、不是环境变量、不是
条件分支：CI、开发机、云主机全部走同一条。之所以刻意不做成可切换开关，是因为"测试构建和
出厂构建用不同方式解析依赖"正是本仓库出过生产事故的那种形态（见 CLAUDE.md「单一构建路径规范」）。
要换源就改这个常量并走一次代码评审，不要引入按环境分叉的开关。

`deploy/cloud/test_cloud_deployment.py::PackageDownloadHost` 会守住以上四点。

### 已知镜像体积问题

`backend/pyproject.toml` 目前把 `playwright==1.61.0` 声明为主依赖，`uv sync --no-dev` 会把它
装进 Control Plane 镜像（wheel 自带 node driver），多背约 50 MB。`control_plane/` 代码里没有
任何 playwright import（只有 `executor/` 用），所以 CLAUDE.md 4.2 的边界在代码层是守住的，
破的是打包层。修法是把 executor 依赖拆成 optional-dependency group，已单独立项 T45 跟踪，
需要 `uv.lock` 无人占用时进行。

## 4. 验收

```bash
AUTOMATION_TOOL_DEMO_LOGIN_NAME=... AUTOMATION_TOOL_DEMO_PASSWORD=... \
  python3 deploy/cloud/verify_cloud_demo.py
```

只走公网 HTTPS：健康、版本、未认证business 读被拒、错误口令被拒、真实账号登录、
带 Session 读业务接口、篡改 token 被拒、refresh 轮换、refresh 重放被拒、注销、
HTTP→HTTPS 308、安全响应头。凭据只从环境变量读，不写入仓库。

契约测试（不需要服务器）：

```bash
python3 -m unittest discover --start-directory deploy/cloud --top-level-directory deploy/cloud
```

## 5. 密钥

`/etc/automation-tool-demo/secrets.json`（0600，root-only）持有 PostgreSQL 超级用户口令、
三个数据库角色口令、账号密码 Pepper、Session 指纹密钥、运维 capability、动作授权私钥、
bootstrap Ed25519 种子与 Demo 账号口令。**不进 Git、不进镜像、不进容器环境变量、不进 argv。**
容器只通过只读卷里的 `/run/secrets/<name>`（uid 65532，0400）读取。

Pepper 或指纹密钥一旦重新生成，所有已存口令哈希与已签发 Session 立即失效，
所以部署器只在缺失时生成，永不覆盖。

## 6. 重启与恢复

`postgres` 与 `control-plane` 都是 `restart: unless-stopped`，`docker.service` 已 `systemctl enable`，
因此主机重启后两个容器自动恢复，nginx 站点是磁盘上的常驻配置。数据在
`automation-tool-demo-postgres-data` 卷中持久化。

回滚：用上一个 commit 重新执行 `deploy.sh`（镜像按 commit 打标签，旧镜像仍在本地）。
数据库不做自动 downgrade；迁移前的验证备份在 `/var/backups/automation-tool-demo/`。

## 7. 禁止事项

* 不 `docker system prune`、不删除任何非本项目创建的容器/网络/卷；
* 不修改主机上既有的 nginx server 块、证书或服务；
* 不向公网暴露 PostgreSQL 或 Control Plane 端口；
* 不把 `secrets.json` 或其中任何值写进仓库、日志或工单。
