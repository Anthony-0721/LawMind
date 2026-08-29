# LawMind

LawMind 是一个面向律所对外咨询场景的多 Agent 智能法律咨询系统。

主要能力：

- 法律问题咨询
- 7 类个人法律领域识别
- AI 初步风险分析
- 律师推荐
- 客户留资
- 咨询记录管理
- FAQ 管理
- 律所工作人员后台
- Docker Compose 一体化部署

文档见 docs/。

## Docker 部署

### 1. 准备环境变量

复制环境变量示例并填写全部敏感配置：

```bash
cp .env.law.example .env
```

启动前必须替换以下值，不要使用示例中的 `REPLACE_ME_*` 占位符：

- `ANTHROPIC_API_KEY`
- `LAWMIND_ADMIN_PASSWORD`
- `LAWMIND_DB_PASSWORD`（PostgreSQL 密码）
- `REDIS_PASSWORD`
- `LAWMIND_SESSION_SECRET`

生成稳定的会话密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

根目录 `docker-compose.yml` 是仓库唯一的 Compose 部署入口；旧的 `backend/docker-compose.yml` 和 `frontend/docker-compose.yml` 已从仓库移除。

### 2. 启动

在仓库根目录执行：

```bash
docker compose up -d --build
```

停止服务：

```bash
docker compose down
```

如需同时清除数据库、Redis 和 ChromaDB 数据卷：

```bash
docker compose down -v
```

### 3. 访问地址

| 服务 | 地址 | 默认端口 |
| --- | --- | --- |
| 前端 + Nginx | http://localhost/ | 80 |
| API 健康检查 | http://localhost/health | 80 |
| API 文档 | http://localhost/docs | 80 |
| OpenAPI | http://localhost/openapi.json | 80 |
| PostgreSQL | localhost:5433 | 5433 |
| Redis | localhost:6379 | 6379 |
| ChromaDB | http://localhost:8003 | 8003 |

后端 API 和前端服务在 Compose 内部网络运行，宿主机统一通过 80 端口访问。
