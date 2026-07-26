# T18-cloud-deploy 真实云主机 Demo 部署

- 状态：🚧 实现中
- 日期：2026-07-26
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

（部署完成后填写运行输出。）

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

（部署完成后填写。）

## 失败矩阵

（部署完成后填写。）

## 清理

（部署完成后填写。）

## 遗留项

- Control Plane 镜像目前多背约 50 MB：`backend/pyproject.toml` 把 `playwright==1.61.0` 声明为
  主依赖，`uv sync --no-dev` 会装进镜像。`control_plane/` 代码无任何 playwright import（仅
  `executor/` 使用），CLAUDE.md 4.2 的边界在代码层守住、在打包层破了。修法为拆
  optional-dependency group，已由协调方立为独立待办 T45，需 `uv.lock` 无并行占用时进行，
  本任务不动。
- 其余遗留项待部署与验收完成后补充。
