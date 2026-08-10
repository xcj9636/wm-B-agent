# 可靠执行与 Outbox 运维 Runbook

本文覆盖 LLM 调用审计和对外消息 Outbox 的数据库状态机。它提供持久幂等、投递租约、有限重试和死信隔离。`schedule_outreach_task` 与 `AutoSenderSkill` 已切换到 Outbox；新增外发入口仍必须通过 `OutreachQueueService`，不得重新引入生产者直连 SMTP 或 WhatsApp API。

## 1. 数据与隐私边界

- `llm_invocations` 用 `idempotency_key` 和输入的 SHA-256 `input_hash` 判断重复请求，不保存 prompt 明文。
- `llm_attempts` 保存每次调用的 provider/model、状态、延迟和经过清理的错误码。
- `outbox_events` 保存投递所需 payload。生产数据库、备份和读权限必须按敏感业务数据管理；日志、指标和管理员状态接口不得复制 payload。
- `GET /api/v1/admin/reliable-execution/status` 仅限超级管理员，返回状态数量、过期租约数和最早待处理时间，不返回收件人、正文、prompt 或生成结果。
- `GET /api/v1/admin/reliable-execution/dead-letters` 仅限超级管理员，支持 `channel` 和 `limit`，只返回事件/聚合标识、尝试次数、时间和稳定错误码；不返回 payload、business key 或历史原始异常。

## 2. 数据库升级

部署应用前备份 PostgreSQL。Compose 部署由一次性 `migrate` 服务执行
`alembic upgrade head`；backend、Worker 和 Beat 只有在迁移成功后才会启动。
应用进程和管理脚本不得调用 `create_all`。手工部署时，在同版本镜像中执行：

```bash
cd backend
alembic upgrade head
alembic current
```

当前可靠执行迁移为 `0002_reliable_execution`、`0003_outbox_delivery_identity`
和 `0004_outbox_resolution_approvals`。先在影子库演练 upgrade、downgrade、再
upgrade；生产回滚应用前，应先确认旧应用不会误读新状态。降级 `0004` 会删除
审批证据索引和审批记录；存在待审批请求时不得降级。继续降级会删除可靠执行表及
其中的审计/待投递记录，不能在存在未处理事件时执行。

## 3. 生产者事务规则

业务写入和 `OutboxService.enqueue()` 必须使用同一个数据库 session，并由调用方统一 commit。禁止在 enqueue 与业务变更之间提交，否则会出现“业务已成功但事件丢失”或相反状态。

触达业务应调用 `OutreachQueueService.queue()`，由它在同一事务创建 `OutreachLog` 和 `OutboxEvent`。定时批次必须提供稳定的 `schedule_config.idempotency_key`；AutoSender 使用 workflow execution ID 生成业务键。不要用当前时间或随机数作为重试时的幂等键。

账号日额度在入队时通过账号行锁预留，计算口径为 `today_sent + pending + scheduled`。真正发送成功后 Worker 才增加 `today_sent`；死信不会消耗已发送额度。Outbox payload 只能包含投递目标和消息内容，账号凭证不得进入 payload。

批量节流通过每条事件的 `available_at` 持久化，不允许在 producer 或 Celery 任务中 `sleep`。`interval_min`/`interval_max` 按秒配置，系统使用区间中点形成确定性间隔；Worker 重启不会丢失排期。

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

`unknown_after_send` 可能已经被上游接收。把它自动重试会造成重复邮件或 WhatsApp，因此必须 fail-closed。PROCESSING 租约过期同样表示发送结果未知，会转入死信而不是重新领取；Worker 会在同一事务把关联 `OutreachLog` 同步为 failed，并通过 `expired_dead_letter` 计数暴露本批次处理量。

## 5. 监控与告警

轮询管理员状态接口，并至少对以下情况告警：

- `expired_outbox_leases > 0`：立即告警；
- `dead_letter` 持续增长：立即告警并按 channel/provider 聚合调查；
- `oldest_pending_at` 距当前时间超过业务 SLO；
- `llm_invocation_counts.started` 长时间不归零；
- Celery Beat 或 Worker 没有心跳。

管理员接口只提供聚合信号。事件级排查必须在受控后台或只读 SQL 会话进行，避免把 payload 粘贴到工单、聊天或普通日志。

## 6. 死信处置

超级管理员可从前端 `/operations/dead-letters` 进入处置台；直接调用以下接口时仍需
使用超级管理员令牌。前端和 API 都不会显示投递 payload 或 business key。

1. 暂停相关生产者或 channel，使用死信列表取得事件 ID，保留租约 token、attempt count、business key 和时间线；payload 不得复制到工单。
2. 判断上游是否已经接受消息；优先使用上游审计记录或 provider message ID 核对，并把不含密钥/正文的工单或审计路径作为 `evidence_reference`。
3. `permanent` 先修正配置或数据；`unknown_after_send` 在无法证明“未发送”时不得补发。
4. 两名不同的超级管理员必须提交完全相同的处置结论。接口只记录审批和修改数据库状态，不会在 HTTP 请求内调用外部 provider：

   ```http
   POST /api/v1/admin/reliable-execution/dead-letters/{event_id}/resolution-approvals
   Content-Type: application/json

   {
     "action": "confirmed_not_sent",
     "evidence_reference": "provider-audit/INC-1234"
   }
   ```

   - `confirmed_not_sent`：第二人批准后把事件置为 `retry`，由 Worker 按原业务键重新投递；不得提供 `external_message_id`。
   - `confirmed_sent`：必须同时提供已核验的 `external_message_id`；第二人批准后仅将事件及关联触达记录对账为 `sent`，不会再次发送。
   - 同一管理员不能批准两次；两次提交的 action、证据引用和 message ID 任一不同都会返回 `409`。证据引用和消息正文不会出现在响应里。

5. 恢复后观察待处理年龄、死信增量、账号计数和重复投递投诉。

## 7. 回滚

先停止 Beat 和 Worker，防止回滚期间继续领取事件：

```bash
docker compose stop celery_worker
```

回滚应用镜像后，仅在数据模型兼容且旧版本不会直接发送同一业务动作时恢复 Worker。数据库 downgrade 是最后手段；执行前必须导出 `llm_invocations`、`llm_attempts`、`outbox_events` 并确认没有 PENDING、RETRY 或 PROCESSING 事件。

## 8. Agent fast/deep 路由与负载门禁

Agent Chat 在任务入队时把不含原始消息的执行档案固化到 `agent_runs.state_json`。
只有短、低风险且对话历史较浅的请求会进入 fast；敏感信息、业务证据问题、工具动作、
长输入或长会话全部进入 deep。Worker 重试和租约接管读取同一份档案，不重新判定。
`route.selected` 事件暴露路径、稳定原因码和 token/历史上限，但不包含 prompt。

紧急回滚 fast path 时设置 `AGENT_FAST_PATH_ENABLED=false` 并重启 API。已入队任务继续
使用其固化档案，新任务全部 deep。此开关不修改 DLP、ACL、provider 白名单或 system
prompt；任何未知、损坏或未来版本的持久档案也会自动 deep。

发布候选版本应在预生产环境运行真实 API 负载门禁。令牌只能通过环境变量注入：

```bash
cd backend
export B_AGENT_LOAD_TOKEN='<short-lived-preproduction-token>'
PYTHONPATH=. python scripts/load_test_agent_chat.py \
  --base-url https://preprod.example.com \
  --requests 100 \
  --concurrency 8 \
  --max-error-rate 0.01 \
  --max-p95-ttft-ms 3000 \
  --max-p95-e2e-ms 15000 \
  --output ../artifacts/agent-load.json
```

脚本使用固定的非敏感提示词，为每个并发请求创建隔离会话，依次验证 202 入队、durable
SSE 回放、`route.selected`、首个 `message.delta`、完成事件和会话清理。报告只包含聚合
延迟、吞吐、路径分布和稳定错误码；任一 SLO 超限、缺失路由/首 token 事件或清理失败
都会返回非零退出码。不要在生产客户租户或含真实业务数据的会话中运行。
