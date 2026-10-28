# 清洁日用品工单 Agent 1.0

本机演示项目：上传清洁用品售后案例 JSON，FastAPI 校验并保存案例，LangChain 单 Agent 按需调用只读工具查询，生成事实依据、处理建议和回复草稿。SQLite 保存每次分析，网页刷新后仍可查看。不会接入真实平台、发送回复、建物流工单或退款。

## 环境与运行

建议 Python 3.12，PowerShell：

```powershell
cd D:\codex\project\cleaning-ticket-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:DEEPSEEK_API_KEY = "你的密钥"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`，上传 `data/case-001.json`。停止服务按 Ctrl+C。密钥也可保存在未提交的 `.env` 中，但当前程序不会自动加载 `.env`；启动前需写入进程环境变量。未设置密钥时上传仍会保存案例，分析状态会显示 `failed`，不会生成虚假结果。

一次仅支持一份 UTF-8 JSON、单工单，最大 1 MiB；仅接受 `metadata.synthetic=true` 的模拟案例。重复上传生成独立案例和运行记录，不覆盖已有数据。数据库和 JSONL 日志位于 `runtime/`，均不进入 Git。日志按 2 MB 轮转，保留 5 个备份文件；仅存事件类型、工具名与查询状态，不存原始客户文本、密钥或模型推理过程。

## 验证

```powershell
python -m pytest tests -q
python -m evals.run_evals
```

第二条默认只验证 5 个固定模拟评估输入的有效性。设置有效 API Key 后，可运行 `python -m evals.run_evals --live` 调用模型并产生费用；词项评分只作初筛，还需人工核实事实、工具选择和回复质量。模型或 prompt 改动后应重跑固定集。当前环境未设置 DeepSeek 密钥，因此真实模型调用尚未验证，token、费用及业务通过率未知。默认模型名 `deepseek-flash` 以 DeepSeek 当前官方接口文档为准；可通过 `DEEPSEEK_MODEL` 覆盖。

本轮已在 Python 3.12 环境执行可编辑安装、代码测试和固定输入校验。固定集目前覆盖常规、缺订单、缺规则、缺物流数据和提示词注入；**过期规则与运行中工具异常的业务评估仍待补充**，代码测试已经覆盖过期规则判断和失败状态。固定集没有真实商家数据。

## 结构与限制

`app/` 是 HTTP、SQLite 和日志；`context/` 校验输入；`tools/` 只读查询；`agents/` 负责模型判断；`workflows/` 控制状态；`prompts/` 版本化提示词；`tests/` 是程序测试；`evals/` 是业务案例。`skills/` 暂不引入项目内技能，`memory/` 关闭跨工单长期记忆。

1.0 使用 FastAPI 后台任务，服务重启会把未完成的 `running` 运行标记为 `interrupted`，不会自动重试。页面不设账号认证，只适合绑定本机 `127.0.0.1`。模拟规则并非拼多多官方政策；业务状态与分析状态分开。程序目前能验证输出字段、来源名称和禁止声称的动作，但不能自动证明每句事实完全正确，需用评估和人工复核。
