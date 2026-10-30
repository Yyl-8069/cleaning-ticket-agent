# SDD ledger — plan: docs/superpowers/plans/2026-10-30-intent-actions-plan.md

- Baseline: c47c65b, develop, 82 tests passed; existing anyio deprecation warning.
- Ruling: reuse current develop checkout as requested project location; do not create a new worktree or switch user branch. All runtime verification uses isolated databases.
- Ruling: use this durable Markdown ledger in PowerShell rather than Bash-only task scripts. No change to task order or review requirements.
- Pre-flight: tasks 1/2 share normalized issue and condition types; task 3 consumes schema v3; task 4 preserves original fixed inputs and checks per-issue outcomes.
- Ruling: preserve legacy single decisions only for compatibility; production prompt emits issues. Untrusted extra model fields are discarded, never passed to output.
- Ruling: condition deadline must be independently linked to customer text and a valid timestamp; no evidence of delivery alone is not proof of non-delivery. Conservative unknown is preferable to a false met condition.
- Task 1: started.

- Task 1: RED: new module missing; GREEN: 40 tests passed (intent decisions + existing grounding). Normalization strips arbitrary financial/free-text fields.

- Task 2: RED 6 expected failures; GREEN 56 related tests passed. Requested refunds with missing order retain manual handoff. Multi-intent processing and state correction preserve separate raw decisions.

- Task 3: RED new page test failed on missing per-issue section; GREEN full suite 99 passed. Prompt v3.0 and per-issue handoff view added; legacy rendering retained.

- Task 4: new per-issue scorer tests initially failed (KeyError due to old single-item interface); after update 109 tests passed, 20 input validations and 20 offline replays passed.
- Ruling: refund tool whitelist, arbitrary-field stripping, and fixed-prose safeguards already existed after tasks 1–3; eight explicit adversarial tests passed immediately as characterization, not falsely claimed RED-GREEN changes.
- Live validation: DEEPSEEK_API_KEY absent in current process (presence-only check); no new model calls, no claim of model quality or semantic review completion.
- Original six inputs and legacy expectations preserved. Added nine separate multi-intent and security cases.
- Task 4: implementation and offline checks complete; live validation pending external key availability. Final whole-branch review follows.


## 最终独立审查与一次修正

- Reviewer: 独立只读审查（gpt-6-astra），范围 c47c65b..b661517；审查实际调用链并复现，无生产资金工具。初始结论需修正条件判断后再交付。
- Findings: P1 日期和退款同现误作送达条件；P1 签收早于发货或与轨迹矛盾仍认作有效；P2 未来消息当相对日期基准；P2 已满足条件意向被称为明确申请。
- RED: 7条条件及意向测试失败，另1条共用签收事实测试失败；均命中真实断言而非环境问题。
- GREEN: 共用 valid_signed_time 校验关联、发货/签收和轨迹时间；条件仅接受最新有效客户消息中的单一明确表达；含糊或撤回未知；条件成立仍保留 conditional 意向。117条测试通过，20输入与20预设判断回放通过。
- Ruling: 最新消息之外的旧条件不自动继承；表达复杂或多日期时宁可未知，避免把语义猜测当成事实。代价是部分正常复杂工单需客服确认，不具备通用条件解析能力。
- Declined-to-judge rulings: 真实模型识别/抗注入识别率和费用未验证；平台数据认证不具备；真实退款/审批/派单无执行能力；图片识别、复杂条件和定时检查不在本轮范围。没有将这些排除项冒称已实现。
- Ruling: 不做合并、推送或部署，保留 develop 现有工作目录及本地提交；用户当前授权为本地实现。
- Final live/manual semantic validation remains pending: current process has no key. All safety claims limited to tested code boundaries, not universal injection detection.
