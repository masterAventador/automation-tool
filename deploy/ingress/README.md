# Customer Demo HTTPS ingress

Ingress 镜像只接受构建参数 `DEMO_HOST=api.<customer-domain>`，由 `render_config.py` 进行 canonical 小写 DNS 校验后写入不可变 Nginx 配置。域名不是运行时自由输入；非法、通配、IP、端口、空白或指令注入值都会令构建失败。

容器内部使用非特权 `8080/8443`，部署层只把公网 `80/443` 映射到对应端口。`8080` 仅 308 跳转到固定 HTTPS 域名；业务请求只能经 `8443`，Control Plane `8000` 与 PostgreSQL `5432` 始终留在私网。证书和私钥只以 `/run/secrets/tls.crt`、`tls.key` 只读 Secret mount 提供，不进入镜像、Git、构建参数、环境变量或日志。

固定边界包括 TLS 1.2/1.3、1 MiB body、10 秒客户端 header/body timeout、5 秒上游连接、60 秒发送/读取、每来源 10 r/s + burst 20、20 个并发连接、安全头、未知 Host 421、无 URI 的脱敏 access log，以及 SSE/WebSocket 所需的 HTTP/1.1、Upgrade 与禁用响应 buffering。真实域名、DNS 和公开证书只在 C10-08 获得用户部署授权后配置。
