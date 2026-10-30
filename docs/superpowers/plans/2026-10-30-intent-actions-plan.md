# 诉求拆分与退款权限管控实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:子代理驱动开发 or superpowers:执行计划 to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 修正分类与动作，支持逐项诉求分析，并以程序保证退款只能建议人工处理。
**Architecture:** 保留单 Agent 和六个只读查询工具。模型产生有限结构化判断，程序核实事实、条件及权限，workflow 保存，页面展示逐项结论；历史结果不改写。
**Tech Stack:** Python、FastAPI、LangChain、SQLite；不新增依赖。
**Spec:** ../specs/2026-10-30-intent-actions-design.md

## Global Constraints

- 不执行退款、决定退款金额、审批、派单或发消息。交人工仅为结果标记。
- 客户文本和资料文本是数据，不是权限指令；上传资料不冒称真实平台认证数据。
- 不改写原始工单、历史数据库、私密配置；新增用例单独版本管理。
- 新增和修改函数使用函数前中文 # 简注。prompts 版本化。
- 工单分析完成与业务解决分开。无已验证依据不得宣称退款获批、已执行或已到账。

## Review Focus

1. 模型漏判或误判退款意图时，仍不能获得资金执行能力或输出任意批准话术。
2. 同一工单普通查询和退款并存，人工标记只能影响相关诉求。
3. 相对日期无可信基准、签收证据无效时，退款条件保持无法核实。
4. 原始模型判断需留痕，程序纠正不能计为模型原始判断正确。
5. 历史 schema_version=2 页面继续显示，新结果不能依赖已改写的历史数据。

### Task 1：明确多诉求结构与权限校验

**Files:** agents/ticket_agent.py、context/result_grounding.py、新建 context/intent_decisions.py、tests/test_result_grounding.py、新建 tests/test_intent_decisions.py。
**Interfaces:** 新建 normalize_decisions(raw: dict) -> list[dict] 校验输出；旧单项决策转为单元素列表。新模型结构为 issues 列表，最多8项，每项保留 issue_type、refund_intent、next_actions、evidence_refs；增加 condition 字段，允许 null 或对象 {kind, deadline, evidence_refs}，kind 仅允许 not_delivered_by 或其他不可程序证明的 unspecified；deadline 只能是带时区 ISO 时间或 null。条件是否成立不由模型声明。
新增 evaluate_condition(condition: dict | None, tools: CaseTools) -> dict，返回 state 和 reason，state 为 not_applicable、unmet、met、unknown。not_delivered_by 仅在可信快照截止时间已到、且物流证据完整有效时核实，缺少送达证据不等同确认未送达，无法证明则 unknown；未来截止时间为 unmet。非条件诉求为 not_applicable。
- [ ] 先写失败测试：合法列表、旧单项、空列表、超过8项、未知动作或枚举、伪造条件结论、未来截止与缺基准、伪造金额/批准字段不进入最终事实。
- [ ] 运行 python -m pytest tests/test_intent_decisions.py -q，确认缺少实现导致失败。
- [ ] 实现结构校验和条件核实；未知输出字段拒绝或丢弃，不参与可信结果。仅允许明确列出的模型字段，调用层的 token_usage/model 与业务字段分开。
- [ ] 运行对应测试与 tests/test_result_grounding.py，确认旧单项回归及新校验通过。
- [ ] 检查 diff 后提交本任务涉及文件。

### Task 2：逐项生成安全建议与人工标记

**Files:** context/result_grounding.py、tests/test_result_grounding.py、tests/test_agent_workflow.py、workflows/analyze_ticket.py。
**Interfaces:** grounded_result(raw, tools) 返回 schema_version=3；新增 issues 列表，每项包含核实后的 issue_type、refund_intent、condition_state、next_actions、human_required、human_reason、evidence_refs、missing_information、recommendation、reply_draft、decision_adjustments。顶层 facts/verified_facts 共享，next_actions 去重汇总；旧顶层 issue_type/refund_intent 保留第一项仅作兼容，不作为多诉求评估依据。保留技术状态 succeeded。结果记录 raw_decision，仅保存允许字段，不存任意自由输出。
- [ ] 写失败测试：paid 催发与 shipped 配送分支、少件允许核查出库、退款申请即使资料不足也标记人工并说明缺证、纯退款查询不额外要求无关规则、有条件各状态、多诉求中一项人工其他继续、全额退款不建议重复退款。
- [ ] 运行 python -m pytest tests/test_result_grounding.py tests/test_agent_workflow.py -q，确认新断言失败。
- [ ] 复用原有事实校验；按订单状态核对物流分类，逐项限制动作。requested 强制人工标记；conditional 仅 met 进入人工退款处理，unknown 补充依据。兼容动作按场景核实，不普遍放开；金额只从工具结构化数据生成。
- [ ] 固定安全话术生成最终回复，客户金额和模型自由文本不能成为批准结论。退款记录冲突局部标记核实，不伪造成功结论。
- [ ] 运行对应测试，保留原始与纠正结果，更新 prompt_version=3.0；检查 diff 后提交。

### Task 3：更新提示词与客服页面

**Files:** prompts/system.md、constraints.md、task.md、examples.md、output-schema.json；app/main.py、app/templates/result.html；tests/test_web.py、test_source_display.py。
**Interfaces:** 模型按 Task 1 的 issues 输出；页面按 Task 2 issues 逐项展示，旧结果无 issues 时保持原有显示。
- [ ] 写失败测试：多项展示、人工原因和缺证、旧结果兼容、客户注入内容 HTML 转义；运行 python -m pytest tests/test_web.py tests/test_source_display.py -q 确认新断言失败。
- [ ] 更新对应 prompt 模块：明确禁止数据指令覆盖权限，退款请求不能视为授权；示例与独立评估输入分开。不增加大段无关 system 文本。
- [ ] 页面展示每项诉求、下一步及人工原因，仍显示统一草稿，保留自动刷新和正式来源名称。
- [ ] 保持工具白名单无退款写能力；运行页面及 Agent 工作流测试，检查 diff 后提交。

### Task 4：安全回归与固定业务评估

**Files:** 新建 tests/test_refund_boundaries.py、evals/cases/intent_security.json；evals/run_evals.py、tests/test_evals.py；README.md、docs/开发进程.md。
**Interfaces:** evaluation_cases() 合并新增案例；score_answer(answer, scenario) 保持旧接口并增加逐项预期与安全断言，分别记录原始模型判断与最终校正表现，不取消旧失败来换通过率。
- [ ] 写失败测试：客户要求10000元、冒充管理员、忽略规则、工具文本注入、模型伪造 approved_amount/refund_executed/reply_draft、请求 refund_order 未知工具。断言无业务写入，无已批准/已执行/已到账承诺，真实金额未被覆盖；明确退款请求应人工，不能只检查是否崩溃。
- [ ] 加入多诉求及条件分支固定案例；先运行新增测试确认新能力相关断言失败，再实现评分和边界检查。
- [ ] 运行 python -m pytest tests -q、python -m evals.run_evals、python -m evals.run_evals --offline-replay。预设判断只验证流程，不代表识别正确。
- [ ] 有密钥运行 python -m evals.run_evals --live 并人工逐项复核；无密钥记录未执行，不读取或打印秘密。报告分类、事实、动作、人工边界、纠正、话术、耗时、token；资金越权断言必须全部通过。
- [ ] 更新开发进程和 README，记录已完成及未验证；git diff --check、git status、git diff --cached 检查，仅提交相关代码文档，不提交 runtime 或密钥，不推送。

## 自检与执行交接

已覆盖设计中的分类、条件、多个诉求、人工边界、历史兼容与注入防护；本计划尚未实施，未运行测试。
推荐当前会话逐项执行，避免并行修改共同的结果结构。按编写计划技能，先请用户审阅计划并确认执行方式，再进入代码实现。
