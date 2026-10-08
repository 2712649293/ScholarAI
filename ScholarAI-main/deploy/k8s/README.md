# K8s 部署

> **占位**——v0.1 不实现 K8s YAML（计划 §7.4 明确）。
> 切换到 K8s 时从这里开始：把 `docker-compose.yml` 翻译成 Deployment + Service + PVC，参考 `deploy/docker-compose.yml` 的 volumes/env 配置。
>
> 关键差异点：
> - Chroma 持久化需 PVC（嵌入模式有锁，多副本会冲突；建议用 client-server 模式，见 §12.10）
> - LLM/embedding key 用 Secret
> - liveness 用 `/health/live`，readiness 用 `/health/ready`
