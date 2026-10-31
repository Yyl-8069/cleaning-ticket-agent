# 用这个项目学习 Git

Git 在本机记录文件版本；GitHub 或 Gitee 是可选的远端存放位置。可以把过程理解为：**查看改动 → 暂存 → 提交到本机 → 推送到远端**。提交和推送是两件事；本项目现在还没有远端地址。

在 PowerShell 中进入项目：

```powershell
cd D:\codex\project\cleaning-ticket-agent
git status
git branch --show-current
```

当前分支叫 `develop`。`git status` 中的 `A` 表示文件已暂存，等待提交。先给**这个仓库**设置作者身份；姓名可用你的英文名或网名，邮箱建议用注册代码托管平台的邮箱。下面的尖括号内容要换成你自己的值，不能原样复制：

```powershell
git config user.name "你的名字或网名"
git config user.email "你的邮箱"
git config --local --list
```

`git config` 没有 `--global`，所以只修改当前项目。它不是平台登录，也不会上传数据。提交前检查文件和测试：

```powershell
git diff --cached --stat
git diff --cached --check
git status
python -m pytest tests -q
python -m evals.run_evals
```

确认 `.env`、`runtime/` 不在提交列表后，创建本地提交：

```powershell
git add .
git commit -m "实现家用电器售后工单 Agent 1.0 本机流程"
git log --oneline -3
git status
```

以后修改代码也是同一循环：`git status` 看变化，`git diff` 看具体内容，运行测试，`git add <文件名>`，`git commit -m "说明本次改动"`。不要用 `git add .` 代替检查；暂存后再用 `git diff --cached` 复核。提交错了先不要运行 `reset --hard` 或强推，说明情况再处理。

准备上传时，先在 GitHub 或 Gitee **创建一个空仓库**，不要勾选初始化 README 或 `.gitignore`。复制平台提供的 HTTPS 仓库地址，然后：

```powershell
git remote add origin 你的HTTPS仓库地址
git remote -v
git push -u origin develop
```

第一次推送通常需要在浏览器完成登录或使用平台的访问令牌；Git 的 `user.email` 不是密码。以后提交后只需 `git push`。如果平台希望首页分支叫 `main`，可以在确认远端为空后先执行 `git branch -m main`，再用 `git push -u origin main`。本项目尚未配置远端，也没有推送。

常见提示：`Author identity unknown` 表示尚未设置上面的姓名/邮箱；`remote origin already exists` 表示已有远端，先用 `git remote -v` 核对；`rejected (fetch first)` 表示远端有你本地没有的提交，先核对远端历史，**不要强推**。不要把 API Key、真实客户资料或运行数据库提交到仓库。
