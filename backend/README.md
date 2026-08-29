# LawMind Backend

LawMind 后端是 Python 3.12 + FastAPI 的单体服务，负责律所法律咨询、多 Agent 路由、RAG、会话记忆、咨询/律师/FAQ 数据、评测和运维接口。部署时由根目录 `docker-compose.yml` 启动，前端和 Nginx 不直接访问内部调试路由。

## 模块

| 模块 | 说明 |
| --- | --- |
| `api/main.py` | FastAPI 入口、生命周期、健康检查、旧版兼容路由与 CLI |
| `api/law_routes.py` | 律所对外 `/law` 与工作人员 `/law/admin` 路由器 |
| `agents/` | 律所 4 类 Agent、共享工具、编排与升级逻辑 |
| `core/` | 法律意图、实体与风险识别、LLM 工具、Skill 加载 |
| `db/` | SQLAlchemy 模型与咨询、律师、FAQ Repository |
| `services/` | 会话身份、咨询记录、律师推荐、FAQ 同步、启动引导 |
| `memory/` | Redis 近期对话与 ChromaDB 长期记忆 |
| `mcp/` | ChromaDB 知识库和 MCP 风格工具管理器 |
| `evaluation/` | 意图评测、LLM-as-Judge、端到端评测和基线比较 |
| `monitor/` | 性能监控、Prometheus 和告警 |
| `data/` | FAQ、律师、领域摘要种子数据以及评测基线 |
| `skills/law_firm/` | 律所人员可维护的 4 个 Skills |
| `tests/` | 后端回归测试 |

## 主要 API

对外客户接口（部署后通过 Nginx 映射为 `/api/law/*`）：

| 方法 | 后端路径 | 说明 |
| --- | --- | --- |
| `POST` | `/law/chat` | 法律咨询对话，返回白名单响应 |
| `GET` | `/law/options` | 领域/紧急度/风险选项 |
| `GET` | `/law/lawyers` | 推荐律师 |
| `POST` | `/law/consultations` | 客户留资 |
| `POST` | `/law/transfer` | 转人工 |

工作人员接口（需要 `X-Admin-Password`）：

| 方法 | 后端路径 | 说明 |
| --- | --- | --- |
| `POST` | `/law/admin/login` | 登录校验 |
| `GET` | `/law/admin/consultations?limit=50` | 咨询记录列表（脱敏） |
| `GET` | `/law/admin/consultations/{id}` | 咨询详情 |
| `PATCH` | `/law/admin/consultations/{id}/status` | 更新状态 |
| `DELETE` | `/law/admin/consultations/{id}` | 删除 |
| `GET` | `/law/admin/lawyers` | 律师列表 |
| `POST` | `/law/admin/lawyers` | 新增律师 |
| `PATCH` | `/law/admin/lawyers/{id}` | 编辑律师 |
| `PATCH` | `/law/admin/lawyers/{id}/toggle` | 启用/停用 |
| `GET` | `/law/admin/faqs?active_only=false` | FAQ 列表 |
| `POST` | `/law/admin/faqs` | 新增 FAQ |
| `PUT` | `/law/admin/faqs/{id}` | 编辑 FAQ |
| `DELETE` | `/law/admin/faqs/{id}` | 删除 FAQ |
| `PATCH` | `/law/admin/faqs/{id}/toggle` | 启用/停用 |
| `POST` | `/law/admin/knowledge/reload` | FAQ -> ChromaDB 同步 |
| `GET` | `/law/admin/metrics` | 概览 |

服务级接口：

- `GET /health`：健康检查
- `GET /docs`、`GET /openapi.json`：FastAPI 文档
- `POST /eval/run`：运行内置评测，返回 pass rate、分数和建议
- `/chat`、`/skills`、`/trace/*`、`/monitor` 等调试/兼容接口仅用于本地开发或 CLI，不作为生产前端入口。

## 评测

- 默认意图用例位于 `evaluation/evaluator.py` 的 `DEFAULT_INTENT_CASES` 和 `DEFAULT_DIALOG_CASES`。
- 只读归档基线：`backend/data/eval/law_baseline.json`。
- 运行时基线：`backend/data/eval/runtime_law_baseline.json`；默认由 `EVAL_BASELINE_PATH` 覆盖，未设置时写入运行时文件。
- 如果运行时基线缺失，`EndToEndEvaluator` 会读取归档基线用于对比，但绝不会覆写归档基线。
- `EndToEndEvaluator.run()` 会运行意图评测；传入 `dialog_cases` 时会调用 LLM Judge；报告只会写入运行时基线用于趋势比较。
- 评测响应元数据包含最终回复；测试要求回复保留“不构成正式法律意见”等法律边界。

## 本地运行

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

如需连接 PostgreSQL/Redis/ChromaDB，从仓库根目录复制 `.env.law.example` 为 `.env` 并设置：

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_MODEL`
- `ANTHROPIC_BASE_URL`
- `DATABASE_URL`
- `REDIS_URL`
- `CHROMA_HOST` / `CHROMA_PORT` / `CHROMA_PERSIST_DIRECTORY`
- `LAWMIND_ADMIN_PASSWORD`
- `LAWMIND_SESSION_SECRET`

## 测试

```powershell
cd backend
python -m pytest tests -v -p no:cacheprovider
```

测试使用 SQLite、内存 Repository 或 Fake LLM Client，不会调用真实 Python/Chroma 服务。

## 安全边界

- `LAWMIND_SESSION_SECRET` 必须显式设置；会话令牌和用户 ID 由稳定密钥派生，数据库只保存令牌哈希。
- 对外 `/law/chat` 响应经过白名单，不泄露 Agent 路由、工具、实体或联系方式。
- 咨询/律师/FAQ 管理接口统一受管理员密码保护。
- FAQ/错误日志会清除手机号、身份证号、邮箱和业务敏感内容。
- 所有法律回复和种子知识都包含“不构成正式法律意见”的提示。
