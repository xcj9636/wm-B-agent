# 可靠执行与 Outbox 运维 Runbook

本文覆盖 LLM 调用审计和对外消息 Outbox 的数据库状态机。它提供持久幂等、投递租约、有限重试和死信隔离，但当前仍是基础设施阶段：遗留的 `schedule_outreach_task` 与 `skill_auto_sender` 尚未全部切换到 Outbox，不能把本文当作“所有发送链路已迁移”的声明。

## 1. 数据与隐私边界

- `llm_invocations` 用 `idempotency_key` 和输入的 SHA-256 `input_hash` 判断重复请求，不保存 prompt 明文。
- `llm_attempts` 保存每次调用的 provider/model、状态、延迟和经过清理的错误码。
- `outbox_events` 保存投递所需 payload。生产数据库、备份和读权限必须按敏感业务数据管理；日志、指标和管理员状态接口不得复制 payload。
- `GET /api/v1/admin/reliable-execution/status` 仅限超级管理员，返回状态数量、过期租约数和最早待处理时间，不返回收件人、正文、prompt 或生成结果。

## 2. 数据库升级

部署应用前备份 PostgreSQL，并在同版本镜像中执行：

```bash
cd backend
alembic upgrade head
alembic current
```

当前可靠执行迁移为 `0002_reliable_execution` 和 `0003_outbox_delivery_identity`。先在影子库演练 upgrade、downgrade、再 upgrade；生产回滚应用前，应先确认旧应用不会误读新状态。降级会删除可靠执行表和其中的审计/待投递记录，不能在存在未处理事件时直接执行。

## 3. 生产者事务规则

业务写入和 `OutboxService.enqueue()` 必须使用同一个数据库 session，并由调用方统一 commit。禁止在 enqueue 与业务变更之间提交，否则会出现“业务已成功但事件丢失”或相反状态。

同一个 `(channel, business_key, event_type)`：

- payload 相同：返回已有事件；
- payload 不同：抛出幂等冲突，调用方必须停止并调查；
- 不得通过生成新的 business key 来掩盖冲突。

## 4. Worker 投递语义

Celery Beat 每 10 秒触发 `dispatch_outbox_task`。Worker 用 `FOR UPDATE SKIP LOCKED` 领取批次，并在执行外部网络调用前提交 PROCESSING 状态和租约；未先提交租约不得发送。

投递结果只分为三类：

| 分类 | 示例 | 动作 |
|---|---|---|
| `retryable_before_send` | 建连失败、连接超时、连接池超时、明确的 HTTP 429 | 指数退避后重试，达到最大次数进入死信 |
| `permanent` | 明确的 HTTP 4xx、无效 channel/payload | 立即进入死信 |
| `unknown_after_send` | HTTP 5xx、读取响应失败、SMTP 返回失败、未分类异常 | 立即进入死信，不自动重试 |

`unknown_after_send` 可能已经被上游接收。把它自动重试会造成重复邮件或 WhatsApp，因此必须 fail-closed。PROCESSING 租约过期同样表示发送结果未知，会转入死信而不是重新领取。

## 5. 监控与告警

轮询管理员状态接口，并至少对以下情况告警：

- `expired_outbox_leases > 0`：立即告警；
- `dead_letter` 持续增长：立即告警并按 channel/provider 聚合调查；
- `oldest_pending_at` 距当前时间超过业务 SLO；
- `llm_invocation_counts.started` 长时间不归零；
- Celery Beat 或 Worker 没有心跳。

管理员接口只提供聚合信号。事件级排查必须在受控后台或只读 SQL 会话进行，避免把 payload 粘贴到工单、聊天或普通日志。

## 6. 死信处置

1. 暂停相关生产者或 channel，保留事件、租约 token、attempt count、business key 和时间线。
2. 判断上游是否已经接受消息；优先使用 `external_message_id` 或上游审计记录核对。
3. `permanent` 先修正配置或数据；`unknown_after_send` 在无法证明“未发送”时不得补发。
4. 当前版本没有自动 requeue API。需要补发时由双人审批生成新的业务动作和审计记录，不直接修改原事件为 PENDING。
5. 恢复后观察待处理年龄、死信增量和重复投递投诉。

## 7. 回滚

先停止 Beat 和 Worker，防止回滚期间继续领取事件：

```bash
docker compose stop celery_worker
```

回滚应用镜像后，仅在数据模型兼容且旧版本不会直接发送同一业务动作时恢复 Worker。数据库 downgrade 是最后手段；执行前必须导出 `llm_invocations`、`llm_attempts`、`outbox_events` 并确认没有 PENDING、RETRY 或 PROCESSING 事件。

