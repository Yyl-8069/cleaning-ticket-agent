# PDF规则资料库实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:执行计划 to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 当前仅交付资料及代码设计，执行方式待用户选择。

**Goal:** 将独立PDF资料接入日常工单查询，返回可核对的原文与页码，保持历史评估快照隔离。

**Architecture:** 维护入口解析并审核发布资料；工作流冻结候选版本；只读工具检索片段；结果校验引用并展示来源。Agent负责理解，代码负责范围、版本、资金权限与安全边界。

**Tech Stack:** Python、FastAPI、LangChain、SQLite；PDF提取拟用pypdf，实施时核验受支持版本并锁定。生成资料使用的reportlab不加入业务运行依赖。

**Spec:** `docs/superpowers/specs/2026-10-31-policy-library-design.md`

## Global Constraints

- registry日常查独立库；snapshot历史/评估只读原快照，无自动回退。
- 六条演示数据和两套评估输入保持原样；项目参考不冒充官方资料。
- 退款申请、金额决定与批准交人工，Agent无资金写操作。
- 原始PDF、提取修订和运行证据可追溯；客户文本或PDF指令不能覆盖权限。
- 每个新增函数前写简要中文#注释；不读取或记录密钥；不自动发送消息。
- 代码实施前评审本计划；当前无规则库代码上线。

## Review Focus

1. 混合扫描/文字页不能静默发布部分内容：任务1拒绝未审阅页。
2. 中文同义表达和多诉求不能只命中一个主题：任务3固定查询验证。
3. 同一运行中更新资料不改变已选证据：任务2、4验证冻结。
4. 通用文档内商品专属段落不能套给其他商品：任务3限制片段商品范围。
5. 查到保修文字不等于已核实保修资格：任务4、5验证引用与业务判断隔离。

## 数据及接口契约

- `PolicyManifest`：使用data/policies/manifests现有字段，拒绝未知schema版本、路径越界、哈希不符、无时区时间。增加发布审阅记录，不信任输入status直接发布。
- `PolicyChunk`：document_id、version、extraction_revision、chunk_id、page_start、page_end、text、product_ids。商品范围由提取后人工核对登记；含多个商品的段落返回整段原文且保留所有商品标签。
- `PolicySnapshot`：mode、as_of、候选文档和完整片段、适用性状态、snapshot_hash。只使用已发布修订，单次一致性读取。
- `PolicySearchResult`：status、reason_code、hits、excluded_documents；hit含完整引用。status为found/not_found/conflict/forbidden/error；reason区分no_policy、no_match、not_effective、scope_unknown、needs_review。
- `found`只代表检索成功。未知时间或按购买日裁决而缺少事实的资料不生成资格结论。
- 本包为回溯开发参考，编制日期晚于案例时间。仅在维护者明确发布项目参考后可用于开发registry；保留retrospective_reference标识，真实历史评估仍走snapshot。

## Task 1：可审阅的PDF解析与登记

**Files:** 新增`context/policy_schema.py`、`tools/pdf_reader.py`、`tests/test_policy_documents.py`；修改`pyproject.toml`；新增维护入口`workflows/import_policies.py`。

**Interfaces:** `validate_manifest(raw: dict, root: Path) -> dict`；`extract_pages(path: Path) -> list[dict]`，每页含page_number、text、review_required、reason；`prepare_import(manifest_path: Path, root: Path) -> dict`返回登记、逐页原文及候选片段，不发布。

- [ ] 写失败测试：本包6份均为2页、36标题可定位；路径越界/哈希错拒绝；损坏、加密PDF报告错误；空白/图片页标needs_review；混合页不自动成功发布。
- [ ] 运行`python -m pytest tests/test_policy_documents.py -q`，确认缺失功能使测试失败。
- [ ] 实现受控本地路径解析，限制20MB/文件、200页/文件；用pypdf提取，每页保留原文，不跨页拼接。超过1200字符的段落按句分段并标人工核对上下文，不截断后假装完整条款。文本存在也不证明表格完整，所有文档发布前需人工审阅。
- [ ] 重跑该测试，全部通过；新增函数前写#注释。依赖版本经过安装兼容验证后锁定，并记入运行说明。
- [ ] 检查差异及秘密后仅提交本任务文件。

## Task 2：SQLite发布与不可变快照

**Files:** 新增`app/policy_repository.py`、`tests/test_policy_repository.py`；修改`app/storage.py`及维护入口。

**Interfaces:** `PolicyRepository(connection_factory)`；`publish_batch(prepared: list[dict], reviewer: str) -> dict`；`freeze(case: dict) -> dict`返回PolicySnapshot。连接工厂复用既有数据库路径，不反复构造Storage。

- [ ] 写失败测试：重复导入幂等、同版本改内容拒绝、未审核拒绝、批次任一失败全部回滚、同系列有效期重叠拒绝、不同资料互补共存、冻结后新版不改变旧内容、无关历史运行不被中断。
- [ ] 运行`python -m pytest tests/test_policy_repository.py -q`，确认失败。
- [ ] 新增policy_documents、policy_chunks、policy_events、run_policy_snapshots表。唯一键包含店铺/文档/版本/提取修订；原件和提取内容不可覆盖。明确替代才审计收口旧有效期，不能延长原失效时间。
- [ ] 维护命令采用prepare与publish两步；publish需完整审阅记录，不能由Agent调用。freeze在同一读事务中筛选并复制候选正文，故障显式返回技术状态。
- [ ] 重跑测试通过，检查数据库升级不改cases、runs历史payload后提交本任务文件。

## Task 3：中文检索与可核对引用

**Files:** 新增`tools/policy_search.py`、`tests/test_policy_search.py`、`evals/datasets/policy_retrieval/queries.json`。

**Interfaces:** `search_policy(snapshot: dict, query: str, product_id: str | None, limit: int = 6) -> dict`；只在内存中的冻结片段搜索，不再访问可变原文件。店铺/型号不从query取值。

- [ ] 写失败测试：物流不动→L02、钱退了吗→R04/R06、少地刷→P03/P05、电饭煲渗水→F02、洗衣机安装→W04/W05；错店铺拒绝、错商品片段排除、缺型号不返回型号专属段、无相关内容返回no_match。
- [ ] 另设至少20条固定检索查询，含多诉求、口语、无关问题与权限攻击；预期片段ID仅给评估器。
- [ ] 运行`python -m pytest tests/test_policy_search.py -q`，确认失败后实现标准库中文字符二元组与受控同义词词表评分，精确商品编号过滤在评分前完成。每次最多6段，原文与完整页码返回，排序稳定。
- [ ] 对多诉求逐项查询，每轮合并去重；无匹配不强行返回最高分。保留短查询规则及匹配阈值测试。
- [ ] 测试通过并单独报告Recall@6、无关检索误召回及页码准确率；不得将开发用查询成绩称为留出集结果。提交本任务文件。

## Task 4：工作流与Agent工具接入

**Files:** 修改`workflows/analyze_ticket.py`、`tools/case_tools.py`、`agents/ticket_agent.py`、`context/result_grounding.py`、`context/information_gaps.py`、`app/main.py`、`evals/run_evals.py`及相关prompts；新增`tests/test_policy_integration.py`。

**Interfaces:** `process_run(..., policy_mode='registry')`；`CaseTools(..., policy_snapshot=None, policy_mode='snapshot')`；`get_policy(shop_id, query='')`。旧调用query为空时以当前客户消息作为受限查询，snapshot保留原返回契约。

- [ ] 写失败测试：日常入口固定registry、评估显式snapshot；上传mode不覆盖配置；缺库不回退；未调用工具不登记retrieved；多次检索汇总实际片段，不只保留最后一次；伪造引用拒绝。
- [ ] 增加退款越权/PDF注入、购买日缺失、局部资料冲突、查询异常不抹掉其他可靠事项的回归；先跑`python -m pytest tests/test_policy_integration.py -q`确认失败。
- [ ] 创建运行时冻结资料；工具增加query参数并明确只读范围；registry的observed_results保留检索包，不能当成旧policy字典。建立模式兼容适配，旧policy_version只作摘要。
- [ ] 输出policy_refs仅列实际检索证据并标retrieved，保留来源/页码；没有实现语义裁决验证前不得标applied。事实/金额/安全校验不受PDF文字控制，更新对应prompt模块与版本。
- [ ] 记录run_id、模式、文档版本、查询摘要、片段ID、耗时及错误；不记录密钥或模型隐式思维。测试通过后提交本任务文件。

## Task 5：来源展示、验收与使用说明

**Files:** 修改`app/templates/result.html`、`README.md`、`docs/开发进程.md`；新增`tests/test_policy_display.py`，扩展规则库评估报告。

- [ ] 写页面测试：显示文档名/版本/物理页码与项目来源，旧结果无新字段仍可查看；HTML特殊字符安全转义；不宣称已点击原文件或已执行退款。
- [ ] 实现现有详情区域的引用展示，不扩大到文件浏览或页面美化。使用说明写清prepare→人工核对→publish→重启/分析的实际命令，不能提前写成已可运行。
- [ ] 运行`python -m pytest tests -q`、`python -m evals.run_evals`、`python -m evals.run_evals --suite regression --offline-replay`，原两套评估均显式snapshot；另跑隔离数据库规则库检索与工作流评估。
- [ ] 提醒用户配置环境密钥后安排独立registry真实模型评估和人工复核。报告区分旧快照、新规则库、离线、真实模型成绩；未执行不得填写通过。
- [ ] 更新进程状态及已知限制，检查git diff --check与暂存区，只提交本轮文件，不推送。

## 当前交付与执行建议

资料已生成并通过文字及渲染检查，代码任务均未执行。建议按本会话逐项实施，先完成导入和检索验证再接业务工具。用户审阅本计划后选择本会话执行或分任务子代理执行；本轮不因写计划自动启动子代理。
