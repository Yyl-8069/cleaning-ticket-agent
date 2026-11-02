# Agency Agents 开发助手接入

配置日期：2026-10-30。范围：当前家用电器售后工单项目的开发辅助与审查。

## 已配置角色

| 文件 | 角色名 | 用途 |
|---|---|---|
| `.codex/agents/ai-engineer.toml` | AI Engineer | Agent判断、提示词、事实校验与业务评估 |
| `.codex/agents/backend-architect.toml` | Backend Architect | FastAPI接口、SQLite、工作流及可观测性 |
| `.codex/agents/frontend-developer.toml` | Frontend Developer | 运营客服页面、交互和可访问性 |
| `.codex/agents/api-tester.toml` | API Tester | 接口、失败路径、测试覆盖与回归审查 |

每份TOML使用 name、description、developer_instructions 三个字段，未指定模型或推理等级，继承调用方设置。角色正文来自本地 Agency Agents，并追加当前项目的中文沟通、技术范围、真实验证和授权约束。角色的通用示例和目标不能被当作本项目已达到的事实。

## 使用方法

- 当前会话可以读取对应角色配置，按其方法协助开发；按实际任务加载一个主要角色即可。
- 示例：“按 Frontend Developer 角色精简客服结果页。”“按 AI Engineer 角色分析催发识别错误。”“按 API Tester 角色只读审查这次接口变更。”
- 当前会话的工作目录是技能工作区，不能据此假定产品项目的原生角色已被自动发现。项目内原生发现需要在以本项目为工作目录的 Codex 会话中验证；必要时新建或重新载入该项目会话。
- 原生自定义Agent是否被当前运行实例识别尚未验证；本轮验证的是配置格式、文件内容和项目入口。没有启动额外模型调用或并行子代理。
- 后续任务先读项目AGENTS.md及开发进程，根据任务选择角色。审查默认只读。角色配置不代表自动授权并行、推送、部署或业务操作。
- 这些是开发期角色配置，产品运行时仍使用现有单Agent工作流；不需要重启FastAPI服务。

官方配置说明：[OpenAI 自定义 Agent](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents)。

## 来源与许可

本地来源为用户已有的 Agency Agents 仓库，原项目：[msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents)。只导入本项目所需四个角色；保留原角色正文并在末尾追加项目适配说明。

导入时本地仓库HEAD记录为 `b37b5bb33b78f2c1d6567d98e25ca818e3259219`；每个实际源文件的SHA-256如下，以本次文件快照为准。这里不声明本地源仓库工作区与该提交完全一致。

| 角色 | 原始相对路径 | 源文件 SHA-256 |
|---|---|---|
| AI Engineer | `engineering/engineering-ai-engineer.md` | `f25a69aad25acd35dd1d62e33d5a814f4a26b075d90f968b4d778452e53924d8` |
| Backend Architect | `engineering/engineering-backend-architect.md` | `45d94306c8f0f4c51efb76066008297fa4e87ce5b69f509cfede224084b74dfb` |
| Frontend Developer | `engineering/engineering-frontend-developer.md` | `71eeb60cbeedd94b41b2e5852d8986c909e8d150c324b4bb99e3b184ae202094` |
| API Tester | `testing/testing-api-tester.md` | `c9e0f993e32242e564900bc7fbf7eb994d18295a92c499762459a15bbec59cb3` |

上游许可：MIT。原版权归属为 AgentLand Contributors，完整原始许可保存在 [Agency Agents LICENSE](third-party/agency-agents-LICENSE.txt)。该第三方许可说明只覆盖导入内容，不替代本项目尚待选择的整体许可证。

## 验证与更新

已使用Python标准库tomllib解析四个配置，核对必需字段、原始角色正文和项目适配约束；未运行第三方安装脚本，也未修改用户全局Codex配置。

本项目保存的是本次本地快照，不自动跟随桌面应用或上游更新。更新角色时先比较内容和许可，再保留项目约束、重新校验TOML并记录Git差异，不能直接覆盖已有配置。
