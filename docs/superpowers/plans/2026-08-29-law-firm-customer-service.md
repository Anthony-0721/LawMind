# LawMind 律所版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建独立运行的 LawMind 律所对外智能法律咨询系统，保留核心多 Agent 架构，新增法律意图、4 个 Agent、律所 Skills、PostgreSQL 咨询/FAQ/律师数据、staff 页面和联合 Docker Compose。

**Architecture:** 保留 MemoryManager -> IntentRecognizer -> AgentOrchestrator -> Agent/Tools -> Memory 的主链路。业务域替换为法律领域；新增 SQLAlchemy + PostgreSQL 持久化层，FAQ 通过同步服务写入 ChromaDB；前端保留 Vue 3，拆成对外客户页和工作人员页。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2.0、PostgreSQL 16、ChromaDB 0.5.23、Redis 7、Vue 3、Vite、Docker Compose。

## Global Constraints

- 在 LawMind 独立仓库实现；原始私有代码不进 LawMind 仓库。
- LawMind 仓库用户可见文件不得出现原项目品牌信息。
- 使用现有 Anthropic 兼容协议；不更换 LLM 服务。
- 所有对外法律回答必须包含“仅供参考，不构成正式法律意见”。
- 只支持中国大陆法律语境。
- 首版只支持 7 类个人法律领域，重点醉驾/危险驾驶。
- PII 首版明文存储，但列表脱敏、禁止写入日志、禁止在对外页面展示。
- 不新增 Java 改造；不接 law-firm-ai；不做真实工单、文件上传、通知、实时人工。
- 保持一个共享 ChromaDB knowledge_base，不按 Agent 拆分知识库。
- FAQ 同步采用同步同步，失败标记 `sync_status=failed`。
- PostgreSQL 宿主机端口使用 `5433:5432`。
- 前端视觉沿用当前风格，不做专业浅色主题重构。

---

## File Structure

**Create:**

```text
backend/core/law_domain.py
backend/db/database.py
backend/db/models.py
backend/db/consultation_repository.py
backend/db/faq_repository.py
backend/db/lawyer_repository.py
backend/services/faq_sync_service.py
backend/services/lawyer_recommendation.py
backend/api/law_routes.py
backend/data/law_faq_seed.json
backend/data/lawyers_seed.json
backend/data/law_domain_briefs/*.md
backend/skills/law_firm/front_desk_reception/SKILL.md
backend/skills/law_firm/criminal_consultation/SKILL.md
backend/skills/law_firm/civil_consultation/SKILL.md
backend/skills/law_firm/escalation_and_intake/SKILL.md
backend/tests/test_law_intent.py
backend/tests/test_law_agents.py
backend/tests/test_law_tools.py
backend/tests/test_law_repositories.py
backend/tests/test_law_api.py
frontend/src/views/CustomerConsultation.vue
frontend/src/views/StaffConsole.vue
frontend/src/lib/lawApi.js
frontend/src/components/LawChatPanel.vue
frontend/src/components/LawyerCard.vue
frontend/src/components/ContactForm.vue
frontend/src/components/ConsultationList.vue
frontend/src/components/FaqManager.vue
frontend/src/components/LawyerManager.vue
frontend/src/components/DebugConsole.vue
docker-compose.yml
.env.law.example
```

**Modify:**

```text
backend/core/intent_recognizer.py
backend/agents/agent_orchestrator.py
backend/agents/tools.py
backend/mcp/knowledge_base.py
backend/mcp/tool_manager.py
backend/api/main.py
backend/requirements.txt
backend/skills/_legacy_customer_support/*
README.md
frontend/package.json
frontend/src/App.vue
frontend/src/main.js
frontend/src/lib/backends.js
frontend/docker-compose.yml
```

---

### Task 0: 初始化 LawMind 独立仓库与品牌清理

**Files:**
- Create: `D:/Project/LawMind`
- Remote: `https://github.com/Anthony-0721/LawMind.git`

**Interfaces:**
- Consumes: current Python and Vue source
- Produces: 独立 git 仓库、backend/frontend 目录、LawMind 品牌

- [ ] **Step 1: 创建本地 LawMind 目录**

Run:

```powershell
New-Item -ItemType Directory -Force -Path 'D:\Project\LawMind'
New-Item -ItemType Directory -Force -Path 'D:\Project\LawMind\backend'
New-Item -ItemType Directory -Force -Path 'D:\Project\LawMind\frontend'
```

- [ ] **Step 2: 复制后端源码**

复制以下内容到 `LawMind/backend`：

```text
api
agents
core
db
mcp
memory
monitor
evaluation
skills
data
config
requirements.txt
Dockerfile
README.md
```

不复制：

```text
.git
.venv
logs
data/chroma
__pycache__
```

- [ ] **Step 3: 复制前端源码**

复制以下内容到 `LawMind/frontend`：

```text
src
package.json
package-lock.json
vite.config.js
Dockerfile
index.html
```

不复制：

```text
.git
node_modules
dist
```

- [ ] **Step 4: 初始化 Git 并添加远程**

```powershell
cd D:\Project\LawMind
git init
git remote add origin https://github.com/Anthony-0721/LawMind.git
```

- [ ] **Step 5: 清理 LawMind 品牌**

修改以下位置：

```text
backend/api/main.py
  FastAPI title -> LawMind
  BANNER -> LawMind

frontend/package.json
  name -> lawmind-frontend

frontend/src/App.vue
  品牌名 -> LawMind

docker-compose.yml
  container_name -> lawmind-*
```

- [ ] **Step 6: 检查品牌残留**

Run:

```powershell
rg -n "LEGACY_BRAND" .
```

Expected: 无输出。如果仍有残留，继续替换为 LawMind。

- [ ] **Step 7: 首次提交**

```powershell
git add .
git commit -m "chore: initialize LawMind repository"
```

- [ ] **Step 8: 设置远程并推送初始提交**

```powershell
git push -u origin main
```

Note: 推送需要 GitHub 凭据和网络权限；如沙箱阻止，请在执行时请求用户授权。

---


### Task 1: 建立分支和基线

**Files:**
- Modify: all existing tracked files remain unchanged
- Test: run existing `tests/test_agent_orchestrator.py`

**Interfaces:**
- Consumes: no prior code
- Produces: `feature/law-consultation` branch, commits after each task

- [ ] **Step 1: 创建分支**

Run:

```bash
git checkout -b feature/law-consultation
```

- [ ] **Step 2: 确认原版测试可运行**

Run:

```bash
cd backend
python -m pytest tests/test_agent_orchestrator.py -v
```

Expected: 当前环境如果缺少依赖，先安装 `requirements.txt`；测试应通过。

- [ ] **Step 3: 提交分支基线**

```bash
git add -A
git commit -m "chore: create law-consultation branch baseline"
```

---

### Task 2: 新增法律领域常量与意图识别

**Files:**
- Create: `backend/core/law_domain.py`
- Modify: `backend/core/intent_recognizer.py`
- Test: `backend/tests/test_law_intent.py`

**Interfaces:**
- Consumes: existing `IntentRecognizer`
- Produces: `LawIntent` 枚举、`LawRiskFlag`、`LawEntityExtractor`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_law_intent.py
from core.intent_recognizer import IntentRecognizer
from core.law_domain import LawIntent, LawRiskFlag

def make_law_recognizer():
    return IntentRecognizer(api_key="test-key", model="test-model")

def test_recognize_dangerous_driving():
    recognizer = make_law_recognizer()
    result = recognizer.recognize_sync("我被查了，可能是醉驾")
    assert result.intent == LawIntent.DANGEROUS_DRIVING
    assert result.intent_group == "criminal"

def test_high_risk_detention():
    result = make_law_recognizer().recognize_sync("家人已经被刑事拘留")
    assert LawRiskFlag.DETENTION in result.risk_flags
    assert result.urgency == "HIGH"
```

Run:

```bash
python -m pytest tests/test_law_intent.py -v
```

Expected: FAIL，`LawIntent` 未定义。

- [ ] **Step 2: 创建 `core/law_domain.py`**

```python
from enum import Enum

class LawIntent(str, Enum):
    DANGEROUS_DRIVING = "dangerous_driving"
    CRIMINAL_DEFENSE = "criminal_defense"
    LABOR_DISPUTE = "labor_dispute"
    MARRIAGE_FAMILY = "marriage_family"
    CONTRACT_DISPUTE = "contract_dispute"
    TRAFFIC_ACCIDENT = "traffic_accident"
    CIVIL_LOAN = "civil_loan"
    LAWYER_APPOINTMENT = "lawyer_appointment"
    LAW_FIRM_SERVICE = "law_firm_service"
    OTHER = "other"

class LawRiskFlag(str, Enum):
    DETENTION = "detention"
    COURT_SOON = "court_soon"
    INJURY = "injury"
    TRAFFIC_ACCIDENT = "traffic_accident"
    FILED = "filed"
    PROSECUTION = "prosecution"
    NO_LAWYER = "no_lawyer"
```

- [ ] **Step 3: 修改 `core/intent_recognizer.py`**

在现有 `IntentRecognizer` 上增加：

```python
from core.law_domain import LawIntent, LawRiskFlag

LAW_TEMPLATES = {
    LawIntent.DANGEROUS_DRIVING: ["酒后开车被查", "醉驾", "危险驾驶"],
    LawIntent.CRIMINAL_DEFENSE: ["刑事拘留", "涉嫌犯罪", "辩护律师"],
    LawIntent.LABOR_DISPUTE: ["劳动仲裁", "拖欠工资", "违法解除"],
    LawIntent.MARRIAGE_FAMILY: ["离婚", "抚养权", "财产分割"],
    LawIntent.CONTRACT_DISPUTE: ["合同纠纷", "违约", "起诉"],
    LawIntent.TRAFFIC_ACCIDENT: ["交通事故", "赔偿", "责任认定"],
    LawIntent.CIVIL_LOAN: ["民间借贷", "欠钱", "借条"],
    LawIntent.LAWYER_APPOINTMENT: ["预约律师", "转人工", "咨询律师"],
    LawIntent.LAW_FIRM_SERVICE: ["怎么收费", "咨询流程", "律所地址"],
}
```

- [ ] **Step 4: 实现风险信号提取**

```python
LAW_RISK_RULES = {
    LawRiskFlag.DETENTION: ["刑事拘留", "被拘留", "已经关了"],
    LawRiskFlag.COURT_SOON: ["明天开庭", "后天开庭", "马上开庭"],
    LawRiskFlag.INJURY: ["受伤", "死亡", "重伤"],
    LawRiskFlag.FILED: ["立案", "已经立案"],
    LawRiskFlag.PROSECUTION: ["审查起诉", "移送检察院"],
}
```

- [ ] **Step 5: 更新 `_intent_group` 与 `_urgency`**

```python
def _law_urgency(message, risk_flags):
    if LawRiskFlag.DETENTION in risk_flags or "紧急" in message:
        return "CRITICAL"
    if LawRiskFlag.COURT_SOON in risk_flags:
        return "HIGH"
    return "MEDIUM"
```

- [ ] **Step 6: 运行测试并通过**

```bash
python -m pytest tests/test_law_intent.py -v
```

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add core/law_domain.py core/intent_recognizer.py tests/test_law_intent.py
git commit -m "feat: add law intent and risk recognition"
```

---

### Task 3: 替换 Agent 类型、Profile 与路由

**Files:**
- Modify: `backend/agents/agent_orchestrator.py`
- Test: `backend/tests/test_law_agents.py`

**Interfaces:**
- Consumes: `LawIntent`
- Produces: `ReceptionAgent`、`CriminalDefenseAgent`、`CivilConsultationAgent`、`EscalationAgent`

- [ ] **Step 1: 写失败测试**

```python
def test_agent_profiles_are_distinct():
    assert ReceptionAgent.profile.role != CriminalDefenseAgent.profile.role
    assert CriminalDefenseAgent.profile.temperature <= 0.2
    assert EscalationAgent.profile.tool_scope == (
        "search_law_knowledge",
        "recommend_lawyer",
        "validate_contact",
        "create_consultation_record",
        "build_handoff_summary",
    )

def test_escalation_returns_handoff_and_flag():
    result = asyncio.run(EscalationAgent(FakeClient(), "test-model").handle(req))
    assert result.escalate is True
    assert "转人工" in result.content
```

- [ ] **Step 2: 修改 `AgentType`**

```python
class AgentType(Enum):
    RECEPTION = "reception"
    CRIMINAL = "criminal"
    CIVIL = "civil"
    ESCALATION = "escalation"
```

- [ ] **Step 3: 新增 4 个 Agent 类**

```python
class ReceptionAgent(BaseAgent):
    agent_type = AgentType.RECEPTION
    profile = AgentProfile(
        role="律所接待与领域判断",
        mission="识别法律领域、确认缺失信息、处理律所服务/收费咨询",
        workflow=("复述诉求", "识别领域", "检查缺失字段", "给出下一步"),
        tool_scope=("search_law_knowledge", "identify_legal_domain",
                    "check_missing_facts", "build_reception_summary"),
        temperature=0.2,
        max_tokens=900,
    )
```

类似实现 `CriminalDefenseAgent`、`CivilConsultationAgent`、`EscalationAgent`。

- [ ] **Step 4: 修改 `_INTENT_ROUTING`**

```python
_INTENT_ROUTING = {
    LawIntent.DANGEROUS_DRIVING: AgentType.CRIMINAL,
    LawIntent.CRIMINAL_DEFENSE: AgentType.CRIMINAL,
    LawIntent.LABOR_DISPUTE: AgentType.CIVIL,
    LawIntent.MARRIAGE_FAMILY: AgentType.CIVIL,
    LawIntent.CONTRACT_DISPUTE: AgentType.CIVIL,
    LawIntent.TRAFFIC_ACCIDENT: AgentType.CIVIL,
    LawIntent.CIVIL_LOAN: AgentType.CIVIL,
    LawIntent.LAWYER_APPOINTMENT: AgentType.ESCALATION,
}
```

- [ ] **Step 5: 修改 `_domain_scores`**

```python
scores[AgentType.CRIMINAL] += 0.8 if intent in {
    LawIntent.DANGEROUS_DRIVING, LawIntent.CRIMINAL_DEFENSE
} else 0.0
scores[AgentType.CIVIL] += 0.8 if intent in {
    LawIntent.LABOR_DISPUTE, LawIntent.MARRIAGE_FAMILY,
    LawIntent.CONTRACT_DISPUTE, LawIntent.TRAFFIC_ACCIDENT,
    LawIntent.CIVIL_LOAN
} else 0.0
```

- [ ] **Step 6: 运行测试并通过**

```bash
python -m pytest tests/test_law_agents.py -v
```

- [ ] **Step 7: 提交**

```bash
git add agents/agent_orchestrator.py tests/test_law_agents.py
git commit -m "feat: replace agents with law firm roles"
```

---

### Task 4: 替换 Agent 工具与工具白名单

**Files:**
- Modify: `backend/agents/tools.py`
- Test: `backend/tests/test_law_tools.py`

**Interfaces:**
- Consumes: `LawIntent`、`LawRiskFlag`
- Produces: `build_shared_law_rag_tools`、`reception_tools`、`criminal_tools`、`civil_tools`、`escalation_tools`

- [ ] **Step 1: 写失败测试**

```python
def test_tool_scopes_are_isolated():
    assert "recommend_lawyer" not in CriminalDefenseAgent(FakeClient(), "test-model").get_tools()
    assert "recommend_lawyer" in EscalationAgent(FakeClient(), "test-model").get_tools()
    assert "search_law_knowledge" in ReceptionAgent(FakeClient(), "test-model").get_tools()
```

- [ ] **Step 2: 新增工具函数**

```python
def identify_legal_domain(req, args):
    return {"intent": req.intent.value, "risk_flags": req.risk_flags}

def check_missing_facts(req, args):
    required = ["case_stage", "incident_time", "city"]
    return {"missing": [f for f in required if not req.entities.get(f)]}

def recommend_lawyer(req, args, lawyer_service):
    return lawyer_service.recommend(req.intent)
```

- [ ] **Step 3: 重建共享工具**

```python
def build_shared_law_rag_tools(tool_manager):
    return {
        "search_law_knowledge": make_tool(
            name="search_law_knowledge",
            description="检索律所 FAQ 和领域知识",
            properties={"query": {"type": "string"}},
            handler=make_rag_handler(tool_manager),
            required=["query"],
        )
    }
```

- [ ] **Step 4: 增加各 Agent 工具集**

```python
def extract_criminal_facts(req, args):
    return {"facts": req.entities, "risk_flags": req.risk_flags}

def check_criminal_stage(req, args):
    stage = req.entities.get("case_stage", "unknown")
    return {"case_stage": stage, "need_confirm": stage == "unknown"}

def assess_criminal_risk(req, args):
    return {"risk_level": req.urgency, "risk_flags": req.risk_flags}

def criminal_tools():
    return {
        "extract_criminal_facts": make_tool(
            "extract_criminal_facts",
            "提取刑事/醉驾关键事实",
            {},
            extract_criminal_facts,
        ),
        "check_criminal_stage": make_tool(
            "check_criminal_stage",
            "检查案件阶段是否已知",
            {},
            check_criminal_stage,
        ),
        "assess_criminal_risk": make_tool(
            "assess_criminal_risk",
            "判断刑事风险等级",
            {},
            assess_criminal_risk,
        ),
    }
```

- [ ] **Step 5: 运行测试并通过**

```bash
python -m pytest tests/test_law_tools.py -v
```

- [ ] **Step 6: 提交**

```bash
git add agents/tools.py tests/test_law_tools.py
git commit -m "feat: add law firm agent tool whitelists"
```

---

### Task 5: 新增律所 Skills 与种子知识库

**Files:**
- Create: `backend/skills/law_firm/**`
- Create: `backend/data/law_faq_seed.json`
- Create: `backend/data/lawyers_seed.json`
- Modify: `backend/mcp/knowledge_base.py`

**Interfaces:**
- Consumes: `KnowledgeBase.add_documents`
- Produces: `load_law_faq_documents`、`load_law_domain_briefs`

- [ ] **Step 1: 新增 4 个 Skill 文件**

示例：

```markdown
---
name: 刑事咨询规范
description: 刑事/醉驾咨询必须遵守的搜集与边界规则
keywords: 醉驾,刑事,拘留,取保候审,开庭
agents: criminal
enabled: true
---

# 刑事咨询规范

- 先确认客户身份：当事人/家属/朋友
- 追问事故、伤亡、拘留、案件阶段
- 不承诺结果
- 不提供正式法律意见
- 高风险优先引导留资/转人工
```

- [ ] **Step 2: 新增种子 FAQ**

```json
[
  {
    "category": "criminal",
    "question": "醉驾被查后一般会经过哪些阶段？",
    "answer": "通常先由公安机关调查，再视情况进入行政、刑事处理程序；是否立案、起诉和审判需结合事实与证据。",
    "keywords": ["醉驾", "阶段", "立案", "审理"]
  }
]
```

- [ ] **Step 3: 修改 `KnowledgeBase._load_default_docs`**

```python
default_docs = load_law_faq_documents()
domain_briefs = load_law_domain_briefs()
self.add_documents(default_docs + domain_briefs)
```

- [ ] **Step 4: 复制原 Skills 到 legacy**

```bash
mkdir -p skills/_legacy_customer_support
git mv skills/general_customer_service skills/_legacy_customer_support/general_customer_service
git mv skills/technical_support skills/_legacy_customer_support/technical_support
git mv skills/billing_support skills/_legacy_customer_support/billing_support
```

- [ ] **Step 5: 验证**

Run:

```bash
python -c "from mcp.knowledge_base import KnowledgeBase; print(KnowledgeBase().doc_count)"
```

Expected: 包含法律 FAQ 和领域摘要。

- [ ] **Step 6: 提交**

```bash
git add skills data mcp/knowledge_base.py
git commit -m "feat: add law skills and seed knowledge"
```

---

### Task 6: PostgreSQL 接入与数据模型

**Files:**
- Create: `backend/db/database.py`
- Create: `backend/db/models.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_law_repositories.py`

**Interfaces:**
- Consumes: SQLAlchemy
- Produces: `Consultation`、`Lawyer`、`Faq`、`SessionLocal`

- [ ] **Step 1: 添加依赖**

```text
sqlalchemy==2.0.35
psycopg2-binary>=2.9.9
```

- [ ] **Step 2: 写失败测试**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base

def test_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()

def test_repository_creates_consultation():
    repo = ConsultationRepository(test_session())
    record = repo.save_sync(
        request_id="req-1",
        contact_name="张*",
        contact_phone="138****1234",
        consent=True,
        legal_domain="dangerous_driving",
        risk_analysis="初步分析",
    )
    assert record.status == "PENDING"
```

- [ ] **Step 3: 创建 `db/database.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./law.db"))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
```

- [ ] **Step 4: 创建 `db/models.py`**

```python
class Consultation(Base):
    __tablename__ = "consultations"
    id = mapped_column(String(36), primary_key=True)
    request_id = mapped_column(String(36), unique=True, index=True)
    contact_name = mapped_column(String(100))
    contact_phone = mapped_column(String(30))
    consent = mapped_column(Boolean, default=False)
    legal_domain = mapped_column(String(40))
    status = mapped_column(String(20), default="PENDING")
    risk_analysis = mapped_column(Text)
    created_at = mapped_column(DateTime, default=datetime.utcnow)

class Faq(Base):
    __tablename__ = "law_faq"
    id = mapped_column(String(36), primary_key=True)
    category = mapped_column(String(40))
    question = mapped_column(Text)
    answer = mapped_column(Text)
    keywords = mapped_column(JSON, default=list)
    active = mapped_column(Boolean, default=True)
    version = mapped_column(Integer, default=1)
    sync_status = mapped_column(String(20), default="pending")
    sync_error = mapped_column(Text, nullable=True)

class Lawyer(Base):
    __tablename__ = "lawyers"
    id = mapped_column(String(36), primary_key=True)
    name = mapped_column(String(100))
    domain = mapped_column(String(40))
    specialties = mapped_column(JSON, default=list)
    intro = mapped_column(Text, nullable=True)
    active = mapped_column(Boolean, default=True)
```

- [ ] **Step 5: 运行测试并通过**

```bash
python -m pytest tests/test_law_repositories.py -v
```

- [ ] **Step 6: 提交**

```bash
git add db requirements.txt tests/test_law_repositories.py
git commit -m "feat: add postgres models and consultation repo"
```

---

### Task 7: FAQ CRUD 与 ChromaDB 同步

**Files:**
- Create: `backend/db/faq_repository.py`
- Create: `backend/services/faq_sync_service.py`
- Test: `backend/tests/test_law_faq.py`

**Interfaces:**
- Consumes: `Faq`、`KnowledgeBase`
- Produces: `FaqRepository`、`FaqSyncService`

- [ ] **Step 1: 写失败测试**

```python
def test_create_faq_and_sync():
    repo = FaqRepository(test_session())
    faq = repo.create(
        category="criminal",
        question="醉驾怎么处理？",
        answer="需要结合事故、酒精检测、阶段判断。",
        keywords=["醉驾"],
        active=True,
    )
    assert faq.sync_status == "pending"
    FaqSyncService(knowledge_base).sync(faq)
    assert faq.sync_status == "synced"
```

- [ ] **Step 2: 实现 `FaqSyncService.sync`**

```python
def sync(self, faq):
    try:
        self.kb.delete_by_metadata({"faq_id": str(faq.id)})
        self.kb.add_documents([{
            "title": faq.question,
            "content": faq.answer,
            "metadata": {
                "faq_id": str(faq.id),
                "category": faq.category,
                "active": faq.active,
            },
        }])
        faq.sync_status = "synced"
    except Exception as exc:
        faq.sync_status = "failed"
        faq.sync_error = str(exc)
    faq.version += 1
    self.session.commit()
```

- [ ] **Step 3: 扩展 `KnowledgeBase.add_documents` 支持 metadata，并增加删除方法**

```python
def add_documents(self, documents, metadatas=None):
    ids = [f"doc-{i}" for i in range(len(documents))]
    docs = [d["content"] for d in documents]
    metas = metadatas or [{"source": "law_faq"} for _ in documents]
    self._collection.add(ids=ids, documents=docs, metadatas=metas)
    return len(ids)
```

```python
def delete_by_metadata(self, where):
    self._collection.delete(where=where)
```

- [ ] **Step 4: 运行测试并通过**

```bash
python -m pytest tests/test_law_faq.py -v
```

- [ ] **Step 5: 提交**

```bash
git add db/services tests/test_law_faq.py
git commit -m "feat: add faq crud and chroma sync"
```

---

### Task 8: 律师服务与咨询保存

**Files:**
- Create: `backend/db/lawyer_repository.py`
- Create: `backend/services/lawyer_recommendation.py`
- Test: `backend/tests/test_lawyer_recommendation.py`

**Interfaces:**
- Consumes: `Lawyer`、`Consultation`
- Produces: `recommend_lawyers`、`save_consultation`

- [ ] **Step 1: 写失败测试**

```python
def test_recommend_lawyer_by_domain():
    repo = LawyerRepository(test_session())
    repo.seed([{"name":"张律师","domain":"criminal","specialties":["醉驾"],"active":True}])
    result = recommend_lawyers(repo, "dangerous_driving")
    assert result[0]["name"] == "张律师"
    assert len(result) <= 3
```

- [ ] **Step 2: 实现推荐服务**

```python
LAW_DOMAIN_TO_LAWYER = {
    "dangerous_driving": "criminal",
    "criminal_defense": "criminal",
    "labor_dispute": "civil",
    "marriage_family": "civil",
    "contract_dispute": "civil",
    "traffic_accident": "civil",
    "civil_loan": "civil",
}

def law_domain_to_lawyer_domain(intent):
    return LAW_DOMAIN_TO_LAWYER.get(intent, "general")

def recommend_lawyers(repo, intent):
    domain = law_domain_to_lawyer_domain(intent)
    return repo.find_active_by_domain(domain)[:3]

class LawyerRepository:
    def __init__(self, session):
        self.session = session

    def seed(self, lawyers):
        for item in lawyers:
            self.session.add(Lawyer(**item))
        self.session.commit()

    def find_active_by_domain(self, domain):
        return [
            {"name": item.name, "domain": item.domain}
            for item in self.session.query(Lawyer).filter_by(domain=domain, active=True)
        ]
```

- [ ] **Step 3: 实现 EscalationAgent 的 `create_consultation_record` 闭包**

```python
def build_escalation_tools(consultation_repo, lawyer_repo):
    async def create_consultation_record(req, args):
        return await consultation_repo.save_from_agent(req, args)

    def recommend_lawyer(req, args):
        return lawyer_repo.recommend(req.intent.value)

    def validate_contact(req, args):
        phone = str(args.get("phone", ""))
        return {"valid": phone.startswith("1") and len(phone) >= 11}

    def build_handoff_summary(req, args):
        return {
            "request_id": req.request_id,
            "intent": req.intent.value if req.intent else "unknown",
            "risk_flags": req.risk_flags,
        }

    return {
        "create_consultation_record": make_tool(
            "create_consultation_record",
            "保存咨询记录",
            {},
            create_consultation_record,
        ),
        "recommend_lawyer": make_tool(
            "recommend_lawyer",
            "推荐对口律师",
            {},
            recommend_lawyer,
        ),
        "validate_contact": make_tool(
            "validate_contact",
            "校验联系方式",
            {},
            validate_contact,
        ),
        "build_handoff_summary": make_tool(
            "build_handoff_summary",
            "生成转人工摘要",
            {},
            build_handoff_summary,
        ),
    }
```

- [ ] **Step 4: 运行测试并通过**

```bash
python -m pytest tests/test_lawyer_recommendation.py -v
```

- [ ] **Step 5: 提交**

```bash
git add db/services tests/test_lawyer_recommendation.py
git commit -m "feat: add lawyer recommendation and consultation save"
```

---

### Task 9: 律所 API 与工作人员鉴权

**Files:**
- Create: `backend/api/law_routes.py`
- Modify: `backend/api/main.py`
- Test: `backend/tests/test_law_api.py`

**Interfaces:**
- Consumes: all repositories/services
- Produces: `/law/chat`、`/law/consultations`、`/law/admin/*`

- [ ] **Step 1: 写失败测试**

```python
def test_public_save_requires_consent():
    client = TestClient(app)
    response = client.post("/law/consultations", json={
        "request_id":"req-1","contact_name":"张*","contact_phone":"123","consent":False
    })
    assert response.status_code == 422

def test_admin_requires_password():
    client = TestClient(app)
    response = client.get("/law/admin/consultations", headers={})
    assert response.status_code == 403
```

- [ ] **Step 2: 实现鉴权依赖**

```python
def require_admin(x_admin_password: str = Header(None)):
    expected = os.getenv("LAW_FIRM_ADMIN_PASSWORD", "")
    if not expected or x_admin_password != expected:
        raise HTTPException(403, "无权限")
```

- [ ] **Step 3: 注册路由**

```python
app.include_router(law_router, prefix="/law", tags=["律所咨询"])
```

- [ ] **Step 4: 运行测试并通过**

```bash
python -m pytest tests/test_law_api.py -v
```

- [ ] **Step 5: 提交**

```bash
git add api/law_routes.py api/main.py tests/test_law_api.py
git commit -m "feat: add law and admin APIs"
```

---

### Task 10: 前端对外客户页和工作人员页

**Files:**
- Create: `frontend/src/views/CustomerConsultation.vue`
- Create: `frontend/src/views/StaffConsole.vue`
- Create: `frontend/src/lib/lawApi.js`
- Create: components listed above
- Modify: `frontend/src/App.vue`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: `/law/*` APIs
- Produces: 两个前端入口、staff tabs

- [ ] **Step 1: 添加 vue-router**

```json
"vue-router": "^4.5.0"
```

- [ ] **Step 2: 新建 lawApi.js**

```js
async function requestJson(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

export async function lawChat(message, conversationId) {
  return requestJson('/law/chat', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({message, conversation_id: conversationId})
  })
}

export async function saveConsultation(payload) {
  return requestJson('/law/consultations', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  })
}
```

- [ ] **Step 3: 新建 `CustomerConsultation.vue`**

组件包含：

```html
<LawChatPanel @complete="onComplete" />
<LawyerCard v-for="lawyer in recommendedLawyers" :lawyer="lawyer" />
<ContactForm @submit="submitConsultation" />
```

- [ ] **Step 4: 新建 `StaffConsole.vue`**

组件包含 tabs：

```html
<ConsultationList />
<LawyerManager />
<FaqManager />
<DebugConsole />
```

- [ ] **Step 5: 新建路由**

```js
const routes = [
  {path:'/', component: CustomerConsultation},
  {path:'/staff', component: StaffConsole}
]
```

- [ ] **Step 6: 构建验证**

Run:

```bash
cd frontend
npm run build
```

Expected: build 通过。

- [ ] **Step 7: 提交**

```bash
git add frontend/src frontend/package.json frontend/package-lock.json
git commit -m "feat: add law customer and staff frontend"
```

---

### Task 11: 联合 Docker Compose

**Files:**
- Modify: `docker-compose.yml`
- Create: `.env.law.example`
- Modify: `backend/Dockerfile`
- Modify: `frontend/docker-compose.yml`

**Interfaces:**
- Consumes: backend and frontend Dockerfiles
- Produces: `lawmind-*` services

- [ ] **Step 1: 后端服务增加 PostgreSQL 环境**

```yaml
postgres:
  image: postgres:16-alpine
  container_name: lawmind-postgres
  ports:
    - "5433:5432"
  environment:
    POSTGRES_USER: ${LAW_DB_USER:-lawuser}
    POSTGRES_PASSWORD: ${LAW_DB_PASSWORD:-lawpass}
    POSTGRES_DB: ${LAW_DB_NAME:-lawmind_law}
  volumes:
    - postgres-data:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U $${LAW_DB_USER:-lawuser} -d $${LAW_DB_NAME:-lawmind_law}"]
```

- [ ] **Step 2: 应用连接 PostgreSQL**

```yaml
lawmind-api:
  environment:
    DATABASE_URL: postgresql://${LAW_DB_USER:-lawuser}:${LAW_DB_PASSWORD:-lawpass}@postgres:5432/${LAW_DB_NAME:-lawmind_law}
    LAW_FIRM_ADMIN_PASSWORD: ${LAW_FIRM_ADMIN_PASSWORD:-changeme}
```

- [ ] **Step 3: 前端服务加入同一 Compose**

```yaml
lawmind-frontend:
  build:
    context: ../frontend
```

- [ ] **Step 4: Nginx 统一代理**

```nginx
location /api/law/ {
    proxy_pass http://lawmind-api:8000/law/;
}
```

- [ ] **Step 5: 配置验证**

Run:

```bash
docker compose config
```

Expected: Compose 配置合法。

- [ ] **Step 6: 提交**

```bash
git add docker-compose.yml Dockerfile .env.law.example frontend/docker-compose.yml
git commit -m "feat: add combined law docker compose"
```

---

### Task 12: 评测用例、文档与最终回归

**Files:**
- Modify: `backend/evaluation/evaluator.py`
- Create: `backend/data/eval/law_baseline.json`
- Modify: `README.md`
- Modify: design docs

**Interfaces:**
- Consumes: all above components
- Produces: law evaluation baseline

- [ ] **Step 1: 替换默认评测用例**

```python
DEFAULT_INTENT_CASES = [
    IntentTestCase("醉驾被查了会怎么样？", "dangerous_driving"),
    IntentTestCase("家人被刑事拘留了", "criminal_defense"),
    IntentTestCase("公司拖欠工资怎么办？", "labor_dispute"),
    IntentTestCase("离婚需要什么材料？", "marriage_family"),
    IntentTestCase("合同纠纷怎么起诉？", "contract_dispute"),
    IntentTestCase("交通事故赔偿怎么算？", "traffic_accident"),
    IntentTestCase("别人借钱不还怎么处理？", "civil_loan"),
]
```

- [ ] **Step 2: 补充免责声明相关检查**

```python
assert "不构成正式法律意见" in response
```

- [ ] **Step 3: 运行全部测试**

```bash
python -m pytest tests -v
```

- [ ] **Step 4: 更新 README 和架构文档**

- [ ] **Step 5: 最终提交**

```bash
git add .
git commit -m "docs: update law firm project documentation"
```

---

## 自检

- 每个 Task 都有可运行测试。
- 所有对外法律回答都携带免责声明。
- LawMind 独立仓库开发，不在原项目 main 上实施。
- PostgreSQL `5433` 与现有 law-firm-ai `5432` 不冲突。
- 保留原 `EscalationAgent` 作为第 4 个 Agent。
- 保留一个共享 Chroma knowledge_base。


