# 机智 — 个人嵌入式开发助手

基于 [LingQue](https://github.com/CodePothunter/lingque) 框架的本地工作流记录。

---

## 新电脑初始化

### 前置条件

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Git

### 1. 克隆源码

```powershell
git clone https://github.com/stbanana/lingque.git
cd lingque
```

### 2. 安装依赖

```powershell
uv sync
```

### 3. 全局安装（可编辑模式）

```powershell
uv tool install --editable .
```

安装完成后 `lq` 命令在任意目录可用。可编辑模式下修改源码立即生效，无需重装。

> 验证安装：`lq --version` 或 `lq list`

### 4. 准备 `.env`

在 lingque 项目根目录创建 `.env`：

```
# LLM API（必填）
ANTHROPIC_BASE_URL=https://your-provider.com/api/anthropic
ANTHROPIC_AUTH_TOKEN=xxxxx
API_FORMAT=anthropic
MODEL=your-model-name        # 必填，例如 doubao-seed-2-0-lite-260428

# 企业微信 AI 机器人（可选）
WECOM_BOT_ID=aibXXXXXXXXXXXXXXXXXXXXXXXX
WECOM_SECRET=XXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### 5. 初始化助手实例

```powershell
lq init --name 机智 --from-env .env
```

> 实例数据存储在 `~/.lq-jizhi/`，包含人格（SOUL.md）、记忆（MEMORY.md）等文件。

### 6. 编辑人格（可选）

```powershell
lq edit 机智 soul
```

---

## 日常使用：在代码工作区打开助手

进入任意代码工程目录，直接启动终端对话：

```powershell
cd E:\your\embedded\project
lq chat '@机智'
```

> **PowerShell 注意**：`@` 在 PowerShell 中是特殊字符，需要用引号包裹：`lq chat '@机智'`。

助手会自动感知当前目录的文件结构（深度 5 层），注入系统提示。对话中可以：

- **查看文件**：`帮我读取 src/main.c`
- **修改文件**：`把 HAL_Delay(1000) 改成 HAL_Delay(500)`（使用 `edit_file` 工具，精确替换）
- **新建文件**：`帮我在 src/ 下新建 utils.c`
- **查看工程结构**：`当前工程有哪些文件？`

单条消息模式（适合脚本调用）：

```powershell
lq chat '@机智' "帮我解释一下 src/main.c 的初始化流程"
```

---

## 修改 lq 源码后同步

由于使用了 `--editable` 安装，修改源码后**无需任何操作**，下次运行 `lq` 即生效。

```powershell
# 修改代码...
git add .
git commit -m "✨【功能】：xxx"
git push origin master   # 推送到 fork
```

如果需要强制重装（例如 pyproject.toml 的依赖有变动）：

```powershell
cd E:\GITOPEN_PROJECT\lingque
uv sync                          # 更新依赖
uv tool install --editable . --force
```

卸载：

```powershell
uv tool uninstall lingque
```

---

## 常用命令速查

| 命令 | 说明 |
|------|------|
| `lq chat '@机智'` | 在当前目录启动工作区对话 |
| `lq chat '@机智' "问题"` | 单条消息模式 |
| `lq edit 机智 soul` | 编辑人格定义 |
| `lq edit 机智 memory` | 查看/编辑长期记忆 |
| `lq status 机智` | 查看运行状态和 API 消耗 |
| `lq start 机智 --adapter wecom` | 以企业微信模式启动守护进程 |
