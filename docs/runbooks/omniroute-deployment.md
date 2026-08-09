# OmniRoute 生产部署与回滚 Runbook

本文只覆盖 B-agent 专用的 OmniRoute 实例。架构边界是“单组织、专用实例、固定业务别名”；不要把它当成跨租户共享控制面。

## 1. 构建并晋级不可变镜像

开发 Compose 固定 OmniRoute 源码 commit `e0ce95c592c00f100f5141371dbda976d678ddee`，便于复现开发环境。生产不能现场从远程 Git 构建，因为上游 Dockerfile 仍可能解析可变基础镜像或包版本。

在受控 CI 中检出该 commit，构建、扫描并推送内部仓库：

```bash
git clone https://github.com/diegosouzapw/OmniRoute.git
cd OmniRoute
git checkout e0ce95c592c00f100f5141371dbda976d678ddee
docker build --target runner-base -t registry.example.com/b-agent/omniroute:e0ce95c592c0 .
docker push registry.example.com/b-agent/omniroute:e0ce95c592c0
docker image inspect registry.example.com/b-agent/omniroute:e0ce95c592c0 --format '{{json .RepoDigests}}'
```

记录扫描报告、SBOM、签名和 registry 返回的 digest。部署变量必须是完整引用，例如：

```bash
export OMNIROUTE_IMAGE='registry.example.com/b-agent/omniroute@sha256:0123456789abcdef...'
```

## 2. 先配置隔离策略，再接业务流量

1. 在专用 OmniRoute 实例中只配置经过审批的 provider 凭证；`ai_provider_egress_network` 只给 OmniRoute 使用，生产防火墙或 egress proxy 还必须把出口限制到这些 provider 域名。普通 Docker bridge 本身不提供域名级 ACL。
2. 创建 B-agent 专用、最小权限、可轮换的 API key，不与管理员或其他应用共享。
3. 创建固定 combo/model 别名 `b-agent-draft-v1` 和 `b-agent-reply-v1`，其候选只能来自审批 provider。
4. 禁止 `auto/*`、免费/无密钥 provider 和未固定的模型名。应用配置本身也会拒绝 `auto/*`。
5. `OMNIROUTE_ALLOWED_PROVIDERS` 必须填写 OmniRoute completion 响应头 `X-OmniRoute-Provider` 的规范化值，例如 `["openai","azure-openai"]`。

应用侧 allowlist 会在响应返回时检测越界，但请求此时已经发给 gateway。真正的发送前隔离依赖专用实例、固定 combo、只装载审批凭证以及出口 ACL；不能只依赖响应校验。

## 3. 准备 secret 与配置

把 B-agent key 写入仅部署账户可读的文件，并设置：

```bash
export B_AGENT_OMNIROUTE_API_KEY_FILE='/secure/path/b-agent-omniroute-api-key'
export OMNIROUTE_ALLOWED_PROVIDERS='["openai"]'
export OMNIROUTE_MODEL_MESSAGE_DRAFT='b-agent-draft-v1'
export OMNIROUTE_MODEL_LIVE_REPLY='b-agent-reply-v1'
export LLM_BACKEND='omniroute'
```

生产 overlay 会将 key 作为 Docker secret 挂载到 `/run/secrets/b_agent_omniroute_api_key`，并清空明文环境变量。

## 4. 变更前检查与部署

先渲染最终配置，确认 `omniroute.build` 已消失、镜像包含 `@sha256`、网关没有 host `ports`：

```bash
docker compose -f docker-compose.yml -f docker-compose.gateway-production.yml --profile gateway config
```

再拉取并启动：

```bash
docker compose -f docker-compose.yml -f docker-compose.gateway-production.yml --profile gateway pull omniroute
docker compose -f docker-compose.yml -f docker-compose.gateway-production.yml --profile gateway up -d omniroute backend celery_worker
```

## 5. 验收门禁

从 backend 容器验证 B-agent key 只能看到固定别名：

```bash
docker compose exec backend sh -lc 'curl -fsS -H "Authorization: Bearer $(cat /run/secrets/b_agent_omniroute_api_key)" http://omniroute:20128/v1/models'
```

随后以超级管理员身份调用：

```text
GET /api/v1/admin/ai-gateway/status
```

只有 `enabled=true`、`ready=true`、`reachable=true`、`issues=[]` 才能进入 canary。再用不含敏感信息的请求验证 draft 和 reply 两个 use case，并确认每次响应都有允许值的 `X-OmniRoute-Provider`；缺少该头时 B-agent 会 fail-closed，不能放量。当前受 provider 校验约束的 streaming 路径默认拒绝，不得绕过。

## 6. backup / restore

升级前同时备份 OmniRoute 数据卷和当前镜像 digest。示例 backup：

```bash
mkdir -p backups
docker run --rm -v b-agent_omniroute_data:/data:ro -v "$PWD/backups":/backup alpine tar czf /backup/omniroute-data.tgz -C /data .
```

恢复前停止 OmniRoute；在临时卷验证备份可解压、权限正确并完成探测后，再替换生产卷。不要把新版本写过的数据目录直接交给不兼容的旧版本。

## 7. rollback

触发条件包括 readiness 失败、provider 越界、错误率/延迟显著上升或固定别名缺失。

1. 将 `LLM_BACKEND=direct` 并重启 backend/worker，先恢复 B-agent 业务路径。
2. 把 `OMNIROUTE_IMAGE` 改回上一已验证的 `@sha256` digest。
3. 仅在确认数据格式兼容时复用现有卷；否则按第 6 节恢复与旧镜像配套的备份。
4. 重新执行 `docker compose config`、`/v1/models`、管理员 status 和 canary 门禁。
5. 保存 request ID、镜像 digest、配置版本和事件时间线；日志中不得记录 prompt、API key 或 provider credential。
