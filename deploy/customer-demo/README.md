# Customer Demo serial deployment

`compose.v1.json` 是 provider-neutral 的单实例运行清单：migration 是显式 one-shot profile，Control Plane 固定 1 replica/1 worker 且不发布端口，Ingress 是唯一端口发布服务。数据库和 application 网络、运行时/migration/TLS Secret volumes 都必须在部署前由云平台创建并按 C10-03～C10-05 配置；清单不创建数据库、不保存 Secret、不接受 Secret 环境变量。

正式执行使用 `scripts/deploy_customer_demo.py`，输入只包含非秘密环境标识、外部资源名、不可变镜像 digest、预期 OCI version/revision、已验证备份 receipt 和公开 CA。执行器以独占锁串行完成 preflight → backup receipt → migration → 单 Control Plane health → Ingress → HTTPS health/version；任一步失败即停止新服务，不自动 downgrade 数据库、不自动扩容或创建第二个业务实例。

`scripts/run_c10_08_acceptance.py` 只在本机临时 Docker 网络中演练同一清单，允许本地 image ID 和 loopback 随机发布端口；它不等价于真实云、DNS、公开证书、云备份或客户环境证据。正式云执行仍必须给出明确 provider/project/environment 目标和对应身份，不能复用 rehearsal 开关。
