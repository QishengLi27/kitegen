# kitegen 战略文档

> 本文是项目唯一权威战略文档，合并并取代 `design-plan.md` 和 `demo-roadmap.md` 的方向部分（两者保留为历史参考）。
> 更新日期：2026-08-16

---

## 1. 目标

两个互相驱动的目标：

1. **kitegen**：持续优化为生产级 agent 框架，独树一帜。
2. **股票助手**：给自己打造一个全能交易搭档（analysis + discipline + alerts，不做真下单）。

核心思路：**股票助手是框架的试金石 — 每个框架能力由真实需求驱动，每个助手功能反过来验证框架定位。**

---

## 2. 定位

> **kitegen = 构建可人机协作的 AI 工作流的 Python 框架。Agent、Task、Crew、Graph 任意组合，一键部署为脚本 / API / Worker / Stream。**

差异化支柱：

| 支柱 | 现状 | 目标 |
|------|------|------|
| **一切皆 Executable** | Agent/Graph/函数统一协议 ✅ | Task/Crew 也走同一协议，Agent 可 delegate 子 Agent |
| **人机协作原生** | `interrupt()` + `resume()` 基础版 | 审批层级、超时升级、编辑中间结果、审计日志 |
| **写一次，到处跑** | 无 | `to_fastapi() / to_worker() / to_cli() / to_sse()` |
| **中文生态优先** | 腾讯行情、A股工具链、DeepSeek 适配 ✅ | 中文文档、A股数据源内置、开源第一中国 agent 框架 |
| **流式可观测** | 两套并行流式机制（技术债） | 统一事件系统：任意嵌套深度的 token/tool/节点事件一条流 |

---

## 3. kitegen 框架方向

### 3.1 已完成 ✅

- Executable 协议 + Context + RetryPolicy + 事件体系（`core.py`）
- `@tool` 装饰器 + 类型注解 schema 推断（`tool.py`）
- LLM 适配器：OpenAI / Anthropic / LiteLLM（`llm.py`）
- Agent 类：ReAct 工具循环、persona 渲染、max_iterations（`agent.py`）
- Graph：流式、中断恢复、条件路由、checkpoint **合并语义**、流取消（`graph.py`）
- Resilience：CircuitBreaker、TokenTracker、Usage 成本核算
- Checkpointer：MemorySaver / PostgresSaver
- **35 个测试全绿**

### 3.2 待做（按优先级）

| # | 工作 | 为什么 |
|---|------|--------|
| F1 | **统一流式事件系统** | 最大技术债。graph 的 ContextVar 队列与 `context.stream()` 两套并存，Agent 嵌套进 graph 时事件丢失，token 是"跑完补发"的假流式。统一后任意嵌套的事件一条流到调用方 |
| F2 | **Adapter 级 token 流式** | LLM adapter 加 `chat_stream()`，Agent 循环发 `TokenEvent` — 真流式 |
| F3 | **发布 PyPI v0.2 + 文档站** | 框架已 600+ 行、测试全绿。不发布就没有用户反馈循环。文档用 MkDocs，配 LangGraph/CrewAI 迁移对照页 |
| F4 | **部署层：`to_fastapi()/to_worker()/to_cli()`** | 杀手锏。LangGraph/CrewAI 停在"库"，部署自己想办法。demo 手写的 FastAPI+SSE 就是蓝图 |
| F5 | **人机协作升级** | 审批层级（不同阈值路由不同人）、超时+escalation、编辑中间结果再返回、审计日志。金融场景"建议卖出，请确认"是刚需 |
| F6 | **Memory 协议** | `Memory(Protocol): add()/get()` + 内置 `BufferMemory`。与 `LLMAdapter` 同一插拔模式 |
| F7 | **结构化输出** | Agent 支持 `output_schema`，产出 `StockAnalysis(rating, rationale, days_plan, weeks_plan)` 这类可验证结果 |
| F8 | **最小可观测性** | 结构化 trace：每节点耗时/token成本/完整 message 记录，JSONL 输出。**不接 OpenTelemetry**（太重），Langfuse 以后做插件 |
| F9 | **checkpoint 版本化** | `save(state, thread_id, step=N)`，支持回放/回滚 |
| F10 | Task / Crew 轻量版 | Task（模板+executable+output_key）、Crew 做 Graph 语法糖，不引入新运行时 |
| F11 | **插件接口** | 等真实需求定义。过早建插件市场 = 机场没建就买飞机 |

---

## 4. 股票助手方向

### 4.1 已完成 ✅

- Portfolio 数据模型 + JSON 持久化（`portfolio.py`）
- 持仓工具：get/list/record/remove/set_stop_loss/set_take_profit（`agents.py`）
- 三步流水线：研究 → 策略 → 个性化建议（kitegen Graph 串联三 Agent）
- 腾讯行情（A股/HK/US）+ smartbox 动态名称解析（`tools.py`）
- 基本面 + 技术面（MA20/50、RSI14、趋势信号）
- React 前端：阶段步进器、持仓芯片（实时盈亏）、markdown 渲染、分析过程收起/展开、终止按钮
- 会话历史 + 当前问题分离（checkpoint merge 语义后同 tid 自然工作）

### 4.2 待做（按优先级）

| # | 工作 | 价值 |
|---|------|------|
| S1 | **盯盘告警**：止损/止盈触发、单日异动、每早 9 点市场简报 | 从"问才答"变"主动找你" — 杀手锏 |
| S2 | **个人投资规则引擎**：把纪律编码（"亏损 8% 必须止损"、"新仓 ≤10% 资金"），建议前检查 | "聊天工具"变"个人交易系统" |
| S3 | **Trade Plan 工具**：entry/stop/target/position size/R:R 结构化输出 | 建议可执行 |
| S4 | **Paper trading + 交易日志**：记录模拟交易，追踪胜率、盈亏比、回撤 | 验证策略再实战 |
| S5 | **A股数据源扩展**：公告/财报（巨潮/东财）、资金流向、龙虎榜、新闻情绪 | 分析深度 |
| S6 | **技术指标扩展**：MACD、KDJ、BOLL、ATR、量比 | 每个都是纯函数 `@tool` |
| S7 | **组合风控**：行业集中度、相关性、权重偏离告警 | 风险意识 |
| S8 | **Watchlist + 条件扫描**：关注股票设置条件，定期扫描匹配 | 机会发现 |
| S9 | **多模态触达**：Telegram bot、邮件日报、语音播报 | 全天候 |
| S10 | **图表可视化**：K线/RSI/资金曲线图 | 一眼看懂 |

数据源原则：**中国优先**。腾讯行情 + 东财/巨潮为主，不引入美股工具链（用户的持仓是 A股+HK+US 混合，腾讯已覆盖）。

---

## 5. 统一执行顺序

每步都是框架能力和助手需求互相驱动：

| 步骤 | 工作 | 框架产出 | 助手产出 |
|------|------|---------|---------|
| 1 | F1 统一流式 + F2 真流式 | 事件系统一致 | UI 真 token 流、嵌套 Agent 事件可见 |
| 2 | F3 发布 PyPI + 文档 | 反馈循环开始 | — |
| 3 | F4 部署层 | `to_fastapi/to_worker` | S1 盯盘告警的 worker 底座 |
| 4 | S1 盯盘告警 | worker runner 实战验证 | 止损/异动/早报 |
| 5 | F5 人机协作升级 | 审批/超时/审计 | "建议卖出，请确认" |
| 6 | S2 规则引擎 + S3 Trade Plan | F7 结构化输出 | 纪律检查 + 可执行建议 |
| 7 | S4 paper trading + S5 数据源 + S6 指标 | F6 Memory、F8 trace、F9 版本化 | 验证策略 + 深度分析 |
| 8 | F10 Task/Crew + F11 插件 | 生态开放 | S8 watchlist、S9 多模态、S10 图表 |

---

## 6. 不做的事（Non-Goals）

- 真下单/券商对接（决策支持，不执行）
- OpenTelemetry 直连（先做最小 trace，以后插件化）
- 美股专用工具链（Alpha Vantage/Finnhub/websocket 行情 — 中国优先）
- 过早的插件市场
- 向量数据库/RAG 框架内置（RAG 是工具模式，不是框架功能）

---

## 7. 文档索引

| 文档 | 状态 |
|------|------|
| `docs/strategy.md` | **权威** — 定位 + 双线方向 + 执行顺序 |
| `docs/design-plan.md` | 历史 — 框架六阶段初稿，已合并进本文 |
| `docs/demo-roadmap.md` | 历史 — 助手五阶段初稿，已合并进本文 |
| `docs/superpowers/specs/*` | 历史 — 已实现的 stream/agent 设计规格 |
