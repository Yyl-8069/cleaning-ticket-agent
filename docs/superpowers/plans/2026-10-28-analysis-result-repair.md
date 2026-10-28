# 工单分析结果修正实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 superpowers:执行计划逐项实施。本文件为方案，尚未实施。

**Goal:** 修正关键词和空查询误判，输出有证据、可执行的客服建议。

**Architecture:** 保持单 Agent。tools 记录查询证据，校验器核验事实，workflow 管分析状态，页面分别展示结果与业务待办。

**Tech Stack:** Python、FastAPI、LangChain、SQLite，不新增依赖。

**Spec:** 项目 AGENTS.md、用户确认的只读分析边界及本轮六条案例误判反馈。

## 全局约束

保留原始案例、数据库和运行记录；不执行退款或发送消息；不虚构平台规则；函数说明采用定义前的中文 # 注释；不记录或提交密钥。

## Review Focus

- 零金额、否定句、条件句不能触发整单复核（任务2）。
- 查空只能证明当前快照缺数据，不能证明现实中未发生（任务2）。
- 退款流水成功不等于银行卡到账（任务2、4）。
- 缺规则不应抹去订单事实（任务2、4）。
- 旧结果保持可读，不自动改写为成功（任务3）。

## 任务1：冻结验收基线

Files: 新建 evals/analysis_expectations.json，修改 tests/test_evals.py。
Interfaces: 每项包含 case_file、required_facts、forbidden_claims、expected_actions，使用文件名关联，避免旧评估案例ID冲突。

- [ ] 添加基线测试：覆盖 case-001.json 至 case-006.json，文件存在且字段完整；运行并观察失败。
- [ ] 加入六例预期：001物流停滞和条件退款；002未发货催发；003已发货退款申请；004漏液补证；005少件补证；006历史退款说明。
- [ ] 标明002、004、005、006缺规则；004、005缺照片；006不能确认到账。不改原始案例。
- [ ] 重跑测试，通过后审阅并提交。

## 任务2：修正事实校验

Files: tools/case_tools.py、agents/ticket_agent.py、prompts/constraints.md、prompts/output-schema.md、tests/test_agent_workflow.py。
Interfaces: 保留 validate_result(result, tools)；CaseTools 增加 observed_results，按工具名保存 status/data/source，保留旧 observed_sources。输出增加 review_items 列表，每项有 field、reason。

- [ ] 添加失败测试：退款0元、否定退款、条件退款不触发复核；可信流水支持历史退款；无流水不得肯定退款；查空只允许缺失说明；forbidden不作事实依据。
- [ ] 运行 tests/test_agent_workflow.py，确认新测试暴露误判。
- [ ] 记录 found/not_found/forbidden 查询结果，区分未查询与查空。
- [ ] 移除关键词命中即覆盖整段建议与草稿的逻辑。对退款金额、完成状态、签收状态核对结构化数据，来源工具名正确不等于内容正确。
- [ ] 保留已核验事实；未核验内容不展示。受影响草稿改为基于可靠事实的安全草稿；真正冲突或不能核验的项目写入 review_items。无法形成安全结果仍可失败。
- [ ] 同步提示词：区分历史事件、建议动作和本次实际执行，允许有依据的历史退款说明。
- [ ] 重跑测试，通过后审阅并提交。

## 任务3：分离分析状态与业务下一步

Files: workflows/analyze_ticket.py、app/main.py、app/templates/result.html、tests/test_web.py、tests/test_agent_workflow.py。
Interfaces: 不破坏性迁移数据库。新结果增加 next_actions 列表，取值 reply/request_information/check_policy/human_approval/check_logistics。安全分析完成由workflow写succeeded；review_items仅表示事实核验问题。

- [ ] 添加失败测试：补照片、缺规则、等待客服执行仍可分析完成；模型故障显示失败与重试；旧needs_information结果可读。
- [ ] 修改workflow状态归属和页面映射，显示可信事实、缺失信息、业务待办、草稿、局部核验原因及耗时ms。
- [ ] 保留技术日志的阶段、工具状态、模型耗时与token，避免记录敏感正文。
- [ ] 重跑相关测试，通过后审阅并提交。

## 任务4：真实回放与交付

Files: evals/run_evals.py、tests/test_evals.py、README.md；需要时在data/variants/增加独立模拟规则完整案例。
Interfaces: 离线输入检查与真实模型评估分开报告；每例输出事实核验、行动合理性、幻觉、耗时、token；费用未知时明确不可用。

- [ ] 添加评分测试：关键词正确但事实错误仍失败；合理补证不能算分析失败。
- [ ] 用任务1的预期评估实际输出，保留缺规则原始样本；新增规则必须明确为模拟店铺约定。
- [ ] 运行 .venv\Scripts\python.exe -m pytest tests -q 与 .venv\Scripts\python.exe -m evals.run_evals。
- [ ] 在安全取得既有密钥的环境运行真实模型评估，人工逐条核对六例。不将离线输入检查冒充业务评估。
- [ ] 验收：服务正常时六例均可完成安全分析；不虚构退款、到账、丢件或规则；每例有对应下一步；技术异常仍可追踪和重试。
- [ ] 对比误复核、事实错误、耗时和token；成本上升时如实说明。
- [ ] 更新README，执行git diff --check，检查暂存差异及秘密后提交，不推送远端。

## 当前边界

本轮只提供方案，未修改业务代码或重新调用模型。优先修复已证实的根因，不新增Agent、向量库或自动模型重试。
