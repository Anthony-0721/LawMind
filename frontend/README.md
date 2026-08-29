# LawMind Frontend

LawMind 前端是 Vue 3 + Vite 单页应用，提供律所对外客户咨询页和工作人员控制台两个入口。

## 页面结构

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/` | 客户咨询页 | 法律对话、领域标签、缺失事实、初步风险分析、律师推荐、留资与转人工 |
| `/staff` | 工作人员控制台 | 管理员密码登录；咨询记录、律师管理、FAQ 管理、知识库/监控 |

`/staff` 使用懒加载；客户页在页面内展示“不构成正式法律意见”的边界提示。

## 架构与 API 路径

- 本地开发：Vite 代理 `/api/law` -> `http://localhost:8000`，并移除 `/api/law` 前缀。
- 生产部署：`frontend/Dockerfile` 构建静态 SPA，`frontend/nginx.conf` 提供 SPA 回退；根目录 `docker-compose.yml` 中的 Nginx 将 `/api/law/*` 代理到后端。
- 前端 API 默认前缀为 `/api/law`，由 `src/lib/lawApi.js` 统一封装：
  - `VITE_LAW_API_URL`：Vite 代理目标后端地址
  - `VITE_LAW_API_BASE_URL`：浏览器请求的 API 前缀
- Vite 仅配置 `/api/law` 代理；LawMind 不依赖其他语言后端接口。

## 对外接口

前端通过 `/api/law` 前缀调用：

- `POST /api/law/chat`
- `GET /api/law/options`
- `GET /api/law/lawyers`
- `POST /api/law/consultations`
- `POST /api/law/transfer`

## 工作人员接口

`src/lib/lawApi.js` 封装了以下工作人员接口，所有接口都要求请求头：

```text
X-Admin-Password: <管理员密码>
```

- `POST /api/law/admin/login`
- `GET /api/law/admin/consultations?limit=50`
- `GET /api/law/admin/consultations/{id}`
- `PATCH /api/law/admin/consultations/{id}/status`
- `DELETE /api/law/admin/consultations/{id}`
- `GET /api/law/admin/lawyers`
- `POST /api/law/admin/lawyers`
- `PATCH /api/law/admin/lawyers/{id}`
- `PATCH /api/law/admin/lawyers/{id}/toggle`
- `GET /api/law/admin/faqs?active_only=false`
- `POST /api/law/admin/faqs`
- `PUT /api/law/admin/faqs/{id}`
- `DELETE /api/law/admin/faqs/{id}`
- `PATCH /api/law/admin/faqs/{id}/toggle`
- `POST /api/law/admin/knowledge/reload`
- `GET /api/law/admin/metrics`

工作人员页只调用受保护的 `/api/law/admin/metrics` 和 `/api/law/admin/knowledge/reload`，不调用未加律所管理权限的 `/skills`、`/trace/tools`、`/monitor` 等端点。

## 开发

```bash
npm ci
npm run dev
```

开发服务器地址：`http://localhost:5173`。

## 构建

```bash
npm run build
npm run preview
```

## 安全注意

- 客户提交姓名、手机号、城市前必须勾选/确认同意联系。
- 客服页面不返回或展示身份证号、银行卡号、密码、验证码等敏感字段。
- 会话令牌由后端签发，前端仅将其保存在本地请求上下文中。
- 管理密码由后端环境变量决定，前端不会持久化密码。

## Docker

生产镜像使用 `frontend/Dockerfile` 和 `frontend/nginx.conf`。完整栈部署请使用仓库根目录 `docker-compose.yml`：
`frontend/docker-compose.yml` 已移除，不再是生产入口。
