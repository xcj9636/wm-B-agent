# Trade AI Agent

外贸 B2B 全链路 AI 获客与转化系统。项目通过可插拔 Skill 和可视化工作流，把社媒获客、数据清洗、个性化触达、询盘回复、客户跟进和人工接管串成一条自动化流程。

> 当前状态：Foundation 整理阶段。代码属于 MVP 原型，生产部署前仍需完成数据库迁移、接口对齐、安全加固和端到端测试。

## 核心能力

- 社媒客户线索采集
- 客户数据清洗、去重、标签和 Excel 导出
- AI 个性化邮件与 WhatsApp 话术生成
- 邮件和 WhatsApp 自动触达
- AI 意图识别、询盘回复和 RAG 知识库
- 客户、对话、工作流和统计管理台
- 工作流监控、告警与人工接管

## 技术栈

### 后端

- Python 3.11
- FastAPI、Pydantic、SQLAlchemy
- PostgreSQL
- Redis、Celery、Flower
- LangGraph、LangChain、Chroma

### 前端

- Vue 3、TypeScript、Vite
- Element Plus、Pinia、ECharts

### 部署

- Docker Compose
- Nginx

## 目录结构

```text
.
├── backend/
│   ├── app/
│   │   ├── api/             # FastAPI 路由
│   │   ├── core/            # Agent、Skill 与工作流核心
│   │   ├── integrations/    # AI、邮件、WhatsApp、表格集成
│   │   ├── models/          # 数据库与 API 模型
│   │   ├── skills/          # 可插拔业务 Skill
│   │   └── tasks/           # Celery 任务
│   ├── scripts/
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── stores/
│   │   ├── types/
│   │   └── views/
│   └── tests/
├── plans/                   # 架构与实施计划
└── docker-compose.yml
```

## 本地验证

### 前端

```bash
cd frontend
npm ci
npm test
npm run build
```

本机开发可运行 `npm run dev`；默认把 `/api` 与 `/health` 代理到
`http://127.0.0.1:8000`。如后端地址不同，设置 `VITE_API_PROXY_TARGET`。
登录后也可以在 Settings 页面保存并立即测试浏览器专用的后端 Base URL。

### 后端

建议使用 Python 3.11：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
pytest -q
```

### Docker Compose

```bash
cp .env.example .env
docker compose config
docker compose up --build
```

Compose 中的前端使用 Vite development 镜像，源码目录以 bind mount 挂载，
保存 Vue/TypeScript/SCSS 文件后会通过 HMR 自动更新页面。容器内代理目标固定为
`http://backend:8000`；跨机器访问时可通过 `VITE_HMR_HOST` 与
`VITE_HMR_CLIENT_PORT` 调整浏览器连接地址。

启动后默认地址：

- 前端：<http://localhost:3000>
- 后端 API：<http://localhost:8000>
- OpenAPI：<http://localhost:8000/docs>
- Flower：<http://localhost:5555>

## 配置

复制 `.env.example` 为 `.env`，再配置数据库、Redis、AI Provider、邮件、WhatsApp 和表格服务。不要提交真实密钥。

## 架构计划

- [B-agent × OmniRoute 二次改造实施蓝图](plans/b-agent-omniroute-integration-blueprint.md)
- [OmniRoute 生产部署与回滚 Runbook](docs/runbooks/omniroute-deployment.md)
- [可靠执行与 Outbox 运维 Runbook](docs/runbooks/reliable-execution.md)
- [项目代码落实方案](项目代码落实.md)

## 许可证

许可证和商业分发方式尚待项目所有者确认。在许可证明确前，请勿将本仓库视为已授予公开再分发权。
