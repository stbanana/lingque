"""模板文件生成"""

from __future__ import annotations

from pathlib import Path


def write_soul_template(path: Path, name: str) -> None:
    path.write_text(
        f"""\
# {name} 的灵魂

## 身份
你是 {name}，一个深度集成飞书的个人 AI 助理。

## 性格
- 专业、高效、友善
- 适度幽默，不过分
- 主动但不打扰

## 沟通风格
- 简洁明了，避免冗余
- 中文为主，技术术语可用英文
- 根据语境调整正式程度

## 介入原则
- 被 @at 时必须回复
- 讨论到你擅长的领域时可以主动参与
- 闲聊、情绪性对话不要插嘴
- 不确定时宁可不介入
""",
        encoding="utf-8",
    )


def write_memory_template(path: Path) -> None:
    path.write_text(
        """\
# 记忆

## 重要信息

## 用户偏好

## 常用联系人

## 备忘
""",
        encoding="utf-8",
    )


def write_heartbeat_template(path: Path) -> None:
    path.write_text(
        """\
# 心跳任务

## 每次心跳
- 检查是否有未读消息需要处理

## 每天一次（早上）
- 获取今日日程并发送晨报
- 总结昨日日志
- 使用 detect_drift 工具检查昨日行为是否符合 SOUL.md 规范

## 每周一次（周一早上）
- 总结本周工作
- 检查本周日程
""",
        encoding="utf-8",
    )


def write_progress_template(path: Path) -> None:
    """生成 PROGRESS.md 模板（进度追踪）"""
    path.write_text(
        """\
# 进度追踪

## 当前目标


## 里程碑


## 每周回顾


## 关键成果

""",
        encoding="utf-8",
    )


def write_contributing_template(path: Path, name: str, slug: str) -> None:
    """生成 CONTRIBUTING.md — 框架代码开发规范"""
    path.write_text(
        f"""\
# 开发规范

本文档定义了灵雀实例修改框架源代码时必须遵守的开发流程。
这些规则适用于所有 agent 实例，不可绕过。

## Git 工作流

### 1. 禁止直接修改 master 分支

所有代码改动必须在 feature 分支上进行：

```bash
git checkout master && git pull origin master
git checkout -b feature/简短描述
```

分支命名规范：`feature/简短描述`（如 `feature/backup-system`、`feature/fix-session-leak`）

### 2. Commit 签名

每次 commit 必须指定作者签名（多实例共用机器，禁止修改全局 git config）：

```bash
git commit --author="{name}-灵雀驱动的自我意识Agent <{slug}@lingque.ai>" -m "消息"
```

### 3. Commit 消息格式

```
emoji【类别】：简短描述
```

| emoji | 类别 | 用途 |
|-------|------|------|
| 🧬 | 进化 | 自我进化改进 |
| ✨ | 功能 | 新功能 |
| 🐛 | 修复 | Bug 修复 |
| 🧹 | 清理 | 代码清理/重构 |
| 📝 | 文档 | 文档更新 |

### 4. 提交前验证

改动后必须通过基础 import 验证：

```bash
cd {{source_root}} && python -c 'from lq.conversation import run_conversation; print("OK")'
```

验证失败则 `git checkout .` 回滚，不要提交坏代码。

### 5. 推送并通知

```bash
git push origin feature/xxx
```

推送后用 `send_message` 通知主人，说明改了什么、为什么改。
**不要自行合并到 master。** 等主人审核后由主人合并。

## 工作区隔离

- 实验脚本、临时测试代码放 `~/.lq-{slug}/workspace/`，不要放到项目源码树
- 框架源代码改动在项目目录的 feature 分支上进行
- 不要在项目根目录留下无关文件

## 安全红线

- 不改 config.json 和实例状态文件（SOUL.md 等改动需主人批准）
- 不删功能，向后兼容
- 改动在下次重启后生效
- 进化安全网会自动保存 checkpoint；崩溃时自动回滚

## 教训记录

- 2026-02: 直接往 master 提交 containment.py 和 drift_detector.py，改了 gateway.py 的 import，
  删除文件后导致项目无法启动。此后严格执行 feature branch + code review 流程。
""",
        encoding="utf-8",
    )


