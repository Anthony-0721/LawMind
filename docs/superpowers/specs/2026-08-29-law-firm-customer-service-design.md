# LawMind 律所版：对外智能法律咨询系统设计文档

- 日期：2026-08-29
- 状态：已确认，待实施
- 项目：LawMind
- 目标：构建一个独立运行的律所对外智能法律咨询系统

---

## 1. 背景与目标

LawMind 是一个多 Agent 法律咨询运行时，覆盖法律意图识别、多 Agent 路由、RAG、分层记忆、动态 Skills、咨询记录和评测。

本次改造的目标：

> 不重写核心架构，而是在保留现有 Agent 运行时的前提下，将业务域从“电商/客服/技术支持”迁移为“中国大陆个人法律问题咨询”。

首版形态：

- 对外客户 Web/H5 智能法律咨询
- AI 初步风险分析
- 推荐律师
- 客户留资
- 律所工作人员后台查看和管理咨询记录
- 简单管理员密码
- 联合 Docker Compose 部署

---

## 2. 范围

### 2.1 包含

- 法律问题咨询对话
- 7 类个人法律领域，重点醉驾/危险驾驶
- 非敏感案情信息采集
- 初步法律风险分析
- 律师推荐
- 客户留资
- PostgreSQL 咨询记录
- FAQ 管理
- FAQ 到 ChromaDB 同步
- 律师管理
- 工作人员页
- 管理员密码
- 转人工 = 高优先级留资
- 知识库/Skills 调试入口
- 联合 Docker Compose

### 2.2 不包含

- 不接微信 / App
- 不接现有 law-firm-ai
- 不做真实案件管理
- 不生成委托代理合同
- 不生成所函、授权委托书、律师函
- 不支持文件上传
- 不做客户登录
- 不做客户历史咨询查询
- 不做短信/邮件/企业微信通知
- 不接实时在线人工客服
- 不做角色权限
- 不做复杂排班/日历
- 不做真实收费支付
- 暂不改造 Java 版

---

## 3. 产品定位

### 3.1 对外客户页

产品名称暂定：

```text
智能法律咨询助手
```

对外不展示：

- 真实律所名称
- 地址
- 律所电话
- 真实律师电话/微信
- Agent / RAG / 工具 / 监控 / 评测信息

### 3.2 工作人员页

入口：

```text
/staff
```

登录方式：

```text
管理员密码，来自环境变量 LAW_FIRM_ADMIN_PASSWORD
```

工作人员页模块：

```text
咨询记录
律师管理
FAQ 管理
知识库维护
调试台
```

---

## 4. 用户角色与主流程

### 4.1 客户角色

客户可以是：

```text
当事人本人
家属
朋友/第三方代询
```

### 4.2 客户流程

```text
客户进入页面
  -> 输入法律问题或点击快捷标签
  -> 自动识别法律领域
  -> 追问缺失关键信息
  -> 判断紧急度/风险信号
  -> 生成初步法律风险分析
  -> 推荐 1-3 位律师
  -> 客户填写姓名、手机号、城市、希望咨询时间、是否同意联系
  -> 留资或选择不留下联系方式
  -> 若 Agent 信息完整，Agent 自动保存
  -> 前端表单兜底保存
  -> 展示提交成功和咨询编号
```

### 4.3 工作人员流程

```text
进入 /staff
  -> 输入管理员密码
  -> 查看咨询记录列表
  -> 列表默认脱敏
  -> 查看详情
  -> 更新状态
  -> 管理律师
  -> 管理 FAQ
  -> 调试知识库/Agent/Skills
```

---

## 5. 法律领域与意图

### 5.1 首版领域

```text
DANGEROUS_DRIVING        醉驾 / 危险驾驶（重点）
CRIMINAL_DEFENSE         刑事辩护
LABOR_DISPUTE            劳动争议
MARRIAGE_FAMILY          婚姻家事
CONTRACT_DISPUTE         合同纠纷
TRAFFIC_ACCIDENT         交通事故
CIVIL_LOAN               民间借贷 / 债务纠纷
LAWYER_APPOINTMENT       预约律师 / 转人工
LAW_FIRM_SERVICE         律所服务/收费/流程咨询
OTHER                    其他 / 暂不明确
```

### 5.2 实体提取

```text
legal_domain
case_stage
party_role
incident_time
city
blood_alcohol
traffic_accident
injury_or_death
detention_status
has_lawyer
disputed_amount
risk_flags
```

### 5.3 紧急度

保留四档：

```text
LOW
MEDIUM
HIGH
CRITICAL
```

新增风险信号：

```text
已刑事拘留
即将开庭
已发生人身伤亡
发生交通事故
已立案
审查起诉阶段
取保候审失败
无律师代理
临近法定期限
情绪激烈/高风险
```

高风险策略：

```text
停止长篇分析
展示紧急提示
引导留资
提供转人工
不承诺结果
```

---

## 6. Agent 设计

### 6.1 Agent 类型

```text
ReceptionAgent
CriminalDefenseAgent
CivilConsultationAgent
EscalationAgent
```

> 律所版第 4 个 Agent 复用原版 EscalationAgent 结构，不新增 ConsultationIntakeAgent；其留资、推荐律师和创建咨询记录能力通过新增工具实现。

### 6.2 Agent 工具白名单

所有 Agent 共享：

```text
search_law_knowledge
```

各 Agent 专属工具：

```text
ReceptionAgent:
  identify_legal_domain
  check_missing_facts
  build_reception_summary

CriminalDefenseAgent:
  extract_criminal_facts
  check_criminal_stage
  assess_criminal_risk

CivilConsultationAgent:
  extract_civil_facts
  determine_procedure
  assess_civil_risk

EscalationAgent:
  recommend_lawyer
  validate_contact
  create_consultation_record
  build_handoff_summary
```

### 6.3 工具隔离机制

沿用原版：

```text
BaseAgent.get_tools() 返回共享工具
子类 get_tools() 叠加专属工具
LLM 只拿到当前 Agent 白名单
非白名单工具调用返回失败
```

### 6.4 数据库注入

需要写数据库的工具通过闭包注入 repository：

```text
Agent 不直接连接数据库
工具 handler 调用注入的 ConsultationRepository / LawyerRepository / FaqRepository
```

---

## 7. Skills

首版 Skills 目录：

```text
skills/law_firm/front_desk_reception/SKILL.md
skills/law_firm/criminal_consultation/SKILL.md
skills/law_firm/civil_consultation/SKILL.md
skills/law_firm/escalation_and_intake/SKILL.md
```

旧客服 Skills 移到：

```text
skills/_legacy_customer_support/
```

Skills 内容必须包含：

```text
角色定位
处理流程
关键案情追问
升级条件
禁止事项
免责声明
敏感信息保护
```

---

## 8. 知识库与 RAG

### 8.1 沿用原版单知识库

```text
ChromaDB collection: knowledge_base
所有 Agent 共用 search_law_knowledge
```

不建立每个 Agent 独立知识库。

### 8.2 首版知识内容

```text
30-50 条 FAQ
7 个领域知识摘要
律所咨询流程
免责声明
留资与转人工流程
```

### 8.3 RAG 链路

```text
查询改写
ChromaDB 默认 Embedding（all-MiniLM-L6-v2）
并行召回
合并去重
LLM Rerank
Top-K
```

### 8.4 Metadata

```text
faq_id
category
agent_domain
doc_type
keywords
source
active
version
```

### 8.5 未命中策略

```text
允许使用模型一般法律知识回答
必须标注“未检索到律所内部资料，以下为一般法律知识”
不得编造具体法条
高风险则转人工
```

---

## 9. PostgreSQL 数据模型

使用 SQLAlchemy 简单建表。

### 9.1 consultations

```text
id
request_id
user_id
contact_name
contact_phone
city
preferred_time
consent
legal_domain
case_stage
risk_flags
fact_summary
risk_analysis
recommended_lawyer_ids
conversation_summary
source
status
created_at
updated_at
```

状态：

```text
PENDING
CONTACTED
BOOKED
CLOSED
```

### 9.2 lawyers

```text
id
name
domain
specialties
intro
phone
wechat
email
active
sort_order
created_at
updated_at
```

### 9.3 law_faq

```text
id
category
question
answer
keywords
source
active
sort_order
version
sync_status
sync_error
last_sync_at
created_at
updated_at
```

---

## 10. API 设计

### 10.1 对外

```text
POST /law/chat
GET  /law/options
GET  /law/lawyers?domain=...
POST /law/consultations
POST /law/transfer
```

### 10.2 内部

```text
POST /law/admin/login

GET    /law/admin/consultations
GET    /law/admin/consultations/{id}
PATCH  /law/admin/consultations/{id}/status
DELETE /law/admin/consultations/{id}

GET    /law/admin/lawyers
POST   /law/admin/lawyers
PATCH  /law/admin/lawyers/{id}
PATCH  /law/admin/lawyers/{id}/toggle

GET    /law/admin/faqs
POST   /law/admin/faqs
PUT    /law/admin/faqs/{id}
DELETE /law/admin/faqs/{id}
PATCH  /law/admin/faqs/{id}/toggle

POST /law/admin/knowledge/reload
GET  /law/admin/metrics
```

### 10.3 原版接口兼容

原版 `/chat` 等接口保留，作为调试/兼容入口；律所主流程使用 `/law/chat`。

### 10.4 认证

内部接口使用：

```text
X-Admin-Password
```

首版不做完整 JWT/角色体系。

---

## 11. FAQ 与 ChromaDB 同步

### 11.1 同步策略

```text
PostgreSQL 为唯一权威数据源
ChromaDB 为检索副本
新增/编辑/删除/启停时同步同步
同步失败标记 sync_status=failed
客户检索时若 ChromaDB 失败，回退 PostgreSQL 关键词检索
```

### 11.2 文档 ID

```text
faq:<faq_id>
```

### 11.3 同步操作

```text
新增：add
编辑：先 delete then add
删除：delete
停用：delete 或 metadata active=false 后过滤
```

---

## 12. 咨询记录保存策略

- Agent 自动保存：仅当姓名、手机号、同意授权齐全时。
- 前端表单保存：客户点击“提交咨询”时。
- 同一次咨询使用 request_id 去重。
- 不保存完整原始对话，只保存结构化摘要。
- 不保存敏感字段到日志。
- 客户不留联系方式时不保存正式咨询记录。

---

## 13. 律师推荐

规则：

```text
domain 匹配
active = true
specialties 命中
sort_order 升序
最多 3 位
```

对外仅展示：

```text
姓名
擅长领域
简介
```

---

## 14. 前端设计

### 14.1 两个入口

```text
/        对外客户咨询页
/staff   律所工作人员页
```

### 14.2 对外页

```text
咨询对话
快捷问题标签
领域识别
关键事实追问
初步分析
免责声明
律师推荐
留资表单
转人工
```

### 14.3 工作人员页

```text
管理员密码登录
咨询记录
律师管理
FAQ 管理
知识库维护
调试台
```

调试台包含：

```text
Agent 状态
路由结果
工具调用 trace
知识检索测试
评测运行
监控数据
后端切换
```

### 14.4 视觉策略

沿用当前前端视觉风格，不做专门的浅色律所专业主题重构。

---

## 15. Docker 部署

采用联合 Docker Compose：

```text
lawmind-frontend
lawmind-nginx
lawmind-api
lawmind-postgres
lawmind-redis
lawmind-chromadb
```

PostgreSQL 宿主机端口使用：

```text
5433:5432
```

避免与现有 law-firm-ai 的 5432 冲突。

---

## 16. 错误处理与降级

```text
LLM 失败：
  不编造答案，返回重试或转人工提示

PostgreSQL 失败：
  对话继续，提示咨询记录暂时无法保存

FAQ 同步失败：
  标记 failed，客户检索回退 PostgreSQL

律师库为空：
  推荐文案改为“已匹配对口律师团队”

ChromaDB 失败：
  回退 PostgreSQL FAQ 关键词检索

高风险：
  停止分析，优先转人工
```

---

## 17. 测试

新增测试：

```text
test_law_intent.py
test_law_agents.py
test_law_tools.py
test_law_faq.py
test_law_sync.py
test_consultation_store.py
test_law_api.py
test_admin_auth.py
test_lawyer_recommendation.py
```

测试覆盖：

```text
7 类领域识别
紧急度/风险信号
Agent 工具白名单
FAQ CRUD
FAQ 同步
咨询记录保存
去重
律师推荐
管理员密码
高风险转人工
PostgreSQL 失败降级
```

---

## 18. 实施顺序

1. 创建 `feature/law-consultation` 分支
2. 将旧 Skills/知识内容移到 `_legacy`
3. 替换意图识别与实体
4. 替换 Agent 类型、Profile、工具白名单
5. 新增律所 Skills
6. 构建法律 FAQ 与领域知识摘要
7. 接入 PostgreSQL + SQLAlchemy
8. 实现 FAQ CRUD 与同步
9. 实现咨询记录保存
10. 实现律师管理与推荐
11. 实现对外 `/law` API
12. 实现工作人员 API
13. 改造前端两个入口
14. 接入联合 Docker Compose
15. 重写评测用例
16. 补充测试与回归
17. 更新 README 和使用文档

---

## 19. 验收标准

首版完成后应满足：

- 客户可以完成一次法律咨询并看到初步分析和律师推荐
- 客户可以留资并得到咨询编号
- 律师工作人员可以查看和管理咨询记录
- 工作人员可以维护律师和 FAQ
- FAQ 修改后可以同步到 ChromaDB
- 高风险案件会触发紧急提示和转人工
- 所有对外回答包含“不构成正式法律意见”提示
- 不展示原客服知识
- 一条命令可以通过 Docker 启动整套系统
- 原始私有代码保留在本地，不进入 LawMind 仓库




