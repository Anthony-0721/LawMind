# LawMind 学习文档

## 1. 项目定位

LawMind 是一个面向律所对外咨询的多 Agent 系统。它的重点不是“调用大模型”，而是把“法律问题识别、知识检索、行业 Agent、人工升级、咨询记录”串成一条可运维的链路。

```
普通聊天：用户 -> LLM -> 回答
LawMind：用户 -> 意图 -> 风险 -> 记忆 -> RAG -> Agent -> 工具 -> 分析 -> 律师/人工 -> 记录
```

## 2. 为什么需要多个 Agent

法律咨询的差异很大：

- 刑事问题关注拘留、取保、起诉、量刑。
- 民事问题关注合同、证据、时效、赔偿、程序。
- 醉驾问题关注酒精含量、立案、吊销驾照、刑事责任。
- 律所服务问题关注收费、预约、流程。
- 高风险问题需要直接转人工。

如果所有问题都由一个模型处理：

- 需要把所有法律规则塞进一个 prompt。
- 规则一多，容易互相冲突。
- 无法通过工具边界限制不同 Agent 的能力。
- 不利于维护、测试和监控。

所以当前拆成 4 类 Agent：

```text
ReceptionAgent
CriminalDefenseAgent
CivilConsultationAgent
EscalationAgent
```

## 3. 主链路如何工作

```text
1. 客户输入问题
2. 系统读取近期记忆
3. LawIntentRecognizer 识别法律领域
4. 系统提取实体和风险信号
5. 按意图决定是否检索知识库
6. AgentOrchestrator 生成 RoutingDecision
7. 单 Agent 或主辅 Agent 并行执行
8. 加载对应 Skills
9. Agent 调用法律工具
10. 生成初步分析、缺失事实和下一步
11. 推荐律师或触发转人工
12. 返回白名单响应
13. 会话写回 Redis/ChromaDB
14. 咨询草稿写入 PostgreSQL
15. Monitor 统计，Evaluator 评测
```

## 4. 为什么先识别领域再检索知识库

如果所有问题都直接检索，会产生大量无关知识。LawMind 的策略是：

```text
先判断 intent / risk
  -> 刑事类：检索刑法、刑诉法、醉驾意见
  -> 民事类：检索劳动争议、合同、交通事故、民间借贷等
  -> 服务类：检索收费、预约、转人工
  -> 转人工：直接升级，不做长篇法律分析
```

这样既降低无效检索，也减少模型被无关内容干扰。

## 5. 为什么 Knowledge Base 需要 PostgreSQL

- FAQ 需要工作人员修改。
- 律师需要维护。
- 咨询记录要保留。
- ChromaDB 更适合“语义检索”，不适合作为业务数据唯一事实源。

因此：

```text
PostgreSQL：事实源
ChromaDB：检索副本
```

FAQ 修改后由 `FaqSyncService` 同步，避免两侧数据长期不一致。

## 6. 为什么需要三层记忆

| 层 | 时间尺度 | 例子 |
| --- | --- | --- |
| 工作记忆 | 本轮/最近几轮 | “刚才说的是醉驾” |
| 情景记忆 | 跨轮/跨会话 | “上个月咨询过取保” |
| 用户画像 | 长期 | “上海用户，关注刑事辩护” |

放在同一个 Redis key 里会导致上下文太长；只放在 ChromaDB 又会增加延迟。所以拆成三层。

## 7. 为什么 Skills 和知识库分开

知识库回答：

```text
法律是什么？
```

Skills 约束：

```text
应该怎么回答、什么时候升级、哪些话不能说。
```

例如刑事咨询 Skill 会约束：

- 不承诺取保、无罪、缓刑。
- 不指导伪造证据。
- 涉及拘留、即将开庭时转人工。

## 8. 为什么对外接口要白名单

LawMind 是面向客户的律所系统，不应该把以下信息返回给客户：

```text
agent_type
primary_agent
supporting_agents
tools_used
routing_reason
entities
latency_ms
tool_traces
```

但客户和前端需要知道“是否建议转人工”，所以公开：

```text
escalated: true/false
```

这是“安全边界”和“业务可用性”的平衡。

## 9. 为什么有 Monitor 和 Evaluator

没有 Monitor 的时候：

```text
某个 Agent 一直失败，但系统还在继续路由过去。
```

没有 Evaluator 的时候：

```text
规则改完不知道效果是变好还是变坏。
```

所以 LawMind 保留：

```text
Monitor -> routing_score -> 路由调整
Evaluator -> LLM-as-Judge -> 回归检测
```

## 10. 一个请求的代码路径

```text
POST /api/law/chat
  -> api/law_routes.py
  -> core/law_domain.py
  -> core/intent_recognizer.py
  -> agents/agent_orchestrator.py
  -> agents/tools.py
  -> mcp/knowledge_base.py
  -> services/consultation_service.py
  -> services/faq_sync_service.py
  -> memory/conversation_memory.py
  -> api/law_routes.py 返回
```

## 11. 面试时要抓住的重点

- 不是“把 prompt 换成法律”。
- 是把一个通用 Multi-Agent Runtime 落地到垂直法律行业。
- 真正的工程价值在路由、工具边界、数据闭环、安全边界和评测。
- 不夸大数据：没有真实压测时，不要声称 95% 准确率。
