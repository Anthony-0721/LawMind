# LawMind Frontend

Vue 3 + Vite 前端，提供律所对外客户咨询页与工作人员控制台两个入口。

## 页面结构

| 路径 | 页面 | 说明 |
| --- | --- | --- |
| `/` | 客户咨询页 | 法律对话、领域标签、缺失事实、初步风险分析、律师推荐、留资与转人工 |
| `/staff` | 工作人员控制台 | 管理员密码登录；咨询记录、律师管理、FAQ 管理、知识库/调试 |

## API 代理

开发服务器使用 Vite proxy：

- `/api/law` -> `http://localhost:8000`，路径前戳会被移除，例如 `/api/law/chat` 请求后端 `/law/chat`
- `VITE_LAW_API_URL` 可覆盖后端地址
- `VITE_LAW_API_BASE_URL` 可覆盖前端使用的 API 前缀

## 对外接口

- `POST /law/chat`
- `GET /law/options`
- `GET /law/lawyers`
- `POST /law/consultations`
- `POST /law/transfer`

## 工作人员接口

所有工作人员接口使用请求头：

```text
X-Admin-Password: <管理员密码>
```

- `POST /law/admin/login`
- `GET /law/admin/consultations`
- `GET /law/admin/consultations/{id}`
- `PATCH /law/admin/consultations/{id}/status`
- `DELETE /law/admin/consultations/{id}`
- `GET /law/admin/lawyers`
- `POST /law/admin/lawyers`
- `PATCH /law/admin/lawyers/{id}`
- `PATCH /law/admin/lawyers/{id}/toggle`
- `GET /law/admin/faqs`
- `POST /law/admin/faqs`
- `PUT /law/admin/faqs/{id}`
- `DELETE /law/admin/faqs/{id}`
- `PATCH /law/admin/faqs/{id}/toggle`
- `POST /law/admin/knowledge/reload`
- `GET /law/admin/metrics`

调试页还会尝试读取原版运行时端点 `/skills` 和 `/trace/tools`；若部署环境未暴露这些端点，页面会显示后端统计与不可用提示。

## 本地开发

```bash
npm install
npm run dev
```

## 构建

```bash
npm run build
```