# 项目开发约定

- 范围：模拟清洁日用品售后工单 1.0；Python、FastAPI、LangChain 单 Agent、SQLite。本项目根目录是唯一产品代码目录，不能把 `D:\codex\skills\superpowers` 当产品仓库。
- 业务流程由 `workflows/` 管状态和停止条件，`agents/` 负责判断，`tools/` 仅查询一类数据，`context/` 管输入验证，`app/` 管 HTTP 与持久化。提示词分别放在 `prompts/`。
- `data/` 和 `evals/cases/` 的模拟样例可版本化。不要改写既有运行数据库或原始客户资料；不要为通过评估随意改预期答案。`.env`、`runtime/`、日志、密钥和私有数据不得提交。`prompts/` 更改需说明版本和重新评估。
- 新增功能后至少运行 `python -m pytest tests -q` 与 `python -m evals.run_evals`；模型、提示词或工具决策更改时，有密钥再运行 `python -m evals.run_evals --live` 并人工复核。没运行的项目如实写明。
- 提交前检查 `git status`、`git diff --check`、`git diff --cached`，确认无秘密与私有数据，更新 README 和 `.env.example`。任何真实店铺接入、自动发消息、退款、外部部署均不属于 1.0。
