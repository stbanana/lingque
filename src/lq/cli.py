"""灵雀 CLI — lq 命令行工具"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import click

from lq.config import (
    LQConfig,
    find_instance,
    load_config,
    load_from_env,
    resolve_home,
    save_config,
    slugify,
)
from lq.templates import (
    write_contributing_template,
    write_heartbeat_template,
    write_memory_template,
    write_soul_template,
)


def _resolve(instance: str) -> tuple[Path, str, LQConfig | None]:
    """解析实例标识 → (home, display_name, config_or_None)

    支持 `@奶油` 和 `@naiyu` 两种写法。
    """
    result = find_instance(instance)
    if result:
        home, cfg = result
        return home, cfg.name, cfg

    # 没有匹配 → 降级：当作 slug 直接拼路径
    identifier = instance.lstrip("@")
    slug = slugify(identifier) if not identifier.isascii() else identifier
    home = resolve_home(slug)
    return home, identifier, None


@click.group()
@click.version_option(package_name="lingque")
def cli() -> None:
    """灵雀 — 个人 AI 助理框架"""


@cli.command()
@click.option("--name", prompt="助理名称", help="助理实例名称（支持中文）")
@click.option("--from-env", type=click.Path(exists=True), help="从 .env 文件读取凭证")
@click.option("--owner", default="", help="主人名（安全相关：用于审批确认。留空则首个私聊用户自动成为主人）")
def init(name: str, from_env: str | None, owner: str) -> None:
    """初始化一个新的灵雀实例"""
    slug = slugify(name)
    home = resolve_home(slug)

    if home.exists():
        if not click.confirm(f"目录 {home} 已存在，是否覆盖?"):
            raise SystemExit(1)

    # 收集凭证
    if from_env:
        config = load_from_env(Path(from_env))
        config.name = name
        config.slug = slug
    else:
        config = LQConfig(name=name, slug=slug)
        config.api.api_key = click.prompt("API Key", hide_input=True)
        config.api.base_url = click.prompt(
            "API Base URL",
            default=config.api.base_url,
        )

    # 创建目录结构
    home.mkdir(parents=True, exist_ok=True)
    for sub in ("memory", "sessions", "sessions/archive", "groups", "logs", "chat_memories"):
        (home / sub).mkdir(parents=True, exist_ok=True)

    # 主人名配置
    if owner:
        config.owner_name = owner

    # 写入配置
    save_config(home, config)

    # 生成模板
    write_soul_template(home / "SOUL.md", name)
    write_memory_template(home / "MEMORY.md")
    write_heartbeat_template(home / "HEARTBEAT.md")

    # 生成开发规范
    contributing_path = home / "CONTRIBUTING.md"
    if not contributing_path.exists():
        write_contributing_template(contributing_path, name, slug)

    # 生成好奇心日志和进化日志
    from lq.prompts import CURIOSITY_INIT_TEMPLATE
    curiosity_path = home / "CURIOSITY.md"
    if not curiosity_path.exists():
        curiosity_path.write_text(CURIOSITY_INIT_TEMPLATE, encoding="utf-8")

    # 生成进化日志
    from lq.prompts import EVOLUTION_INIT_TEMPLATE
    evolution_path = home / "EVOLUTION.md"
    if not evolution_path.exists():
        evolution_path.write_text(EVOLUTION_INIT_TEMPLATE, encoding="utf-8")

    # 生成进度追踪
    from lq.templates import write_progress_template
    write_progress_template(home / "PROGRESS.md")

    click.echo(f"✓ 实例 @{name} (slug: {slug}) 初始化完成")
    click.echo(f"  配置目录: {home}")
    if owner:
        click.echo(f"  主人:      {owner}")
    else:
        click.echo("  主人:      未设置（首个私聊用户将自动成为主人）")
    click.echo()
    click.echo("后续操作:")
    click.echo(f"  编辑人格:   $EDITOR {home}/SOUL.md")
    click.echo(f"  开始聊天:   uv run lq chat @{name}")


@cli.command("list")
def list_instances() -> None:
    """列出所有灵雀实例"""
    found = False
    for entry in Path.home().iterdir():
        if not entry.is_dir() or not entry.name.startswith(".lq-"):
            continue
        config_path = entry / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, encoding="utf-8") as f:
                d = json.load(f)
            if "api" not in d:
                continue
        except (json.JSONDecodeError, KeyError):
            continue

        name = d.get("name", "?")
        slug = d.get("slug", entry.name.removeprefix(".lq-"))
        label = f"@{name}" if name != slug else f"@{slug}"
        if name != slug:
            label += f"  ({slug})"
        click.echo(f"  {label:30s}")
        found = True

    if not found:
        click.echo("  暂无实例。运行 uv run lq init 创建。")


@cli.command()
@click.argument("instance")
def status(instance: str) -> None:
    """显示实例运行状态"""
    home, display, cfg = _resolve(instance)

    if not home.exists():
        click.echo(f"实例 @{display} 不存在", err=True)
        raise SystemExit(1)

    config = cfg or load_config(home)

    click.echo(f"实例: @{config.name}  (slug: {config.slug})")
    click.echo(f"目录: {home}")

    stats_file = home / "stats.jsonl"
    if stats_file.exists():
        try:
            from lq.stats import StatsTracker
            tracker = StatsTracker(home)

            daily = tracker.get_daily_summary()
            click.echo()
            click.echo("--- 今日用量 ---")
            click.echo(f"  调用次数:   {daily['total_calls']}")
            click.echo(f"  输入 Token: {daily['total_input_tokens']:,}")
            click.echo(f"  输出 Token: {daily['total_output_tokens']:,}")
            click.echo(f"  费用估算:   ${daily['total_cost']:.4f}")
            if daily.get("by_type"):
                parts = [f"{k}={v}" for k, v in daily["by_type"].items()]
                click.echo(f"  调用类型:   {', '.join(parts)}")

            monthly = tracker.get_monthly_summary()
            click.echo()
            click.echo(f"--- {monthly['year']}-{monthly['month']:02d} 月度用量 ---")
            click.echo(f"  调用次数:   {monthly['total_calls']}")
            click.echo(f"  输入 Token: {monthly['total_input_tokens']:,}")
            click.echo(f"  输出 Token: {monthly['total_output_tokens']:,}")
            click.echo(f"  费用估算:   ${monthly['total_cost']:.4f}")
        except Exception:
            pass


def _run_local_chat(instance: str, message: str, project_workspace: Path | None = None) -> None:
    """共用逻辑：本地对话（chat / say 共享）"""
    home, display, cfg = _resolve(instance)

    if not home.exists():
        click.echo(f"错误: 实例 @{display} 不存在，请先运行 uv run lq init", err=True)
        raise SystemExit(1)

    config = cfg or load_config(home)

    from lq.conversation import run_conversation
    asyncio.run(run_conversation(home, config, single_message=message,
                                 project_workspace=project_workspace))


@cli.command()
@click.argument("instance")
@click.argument("message", required=False, default="")
@click.option("--workspace", type=click.Path(exists=True, file_okay=False), default=None,
              help="项目工作区路径（默认为当前目录）")
def chat(instance: str, message: str, workspace: str | None) -> None:
    """和灵雀聊天（本地终端）

    \b
    交互模式:  lq chat @name
    单条模式:  lq chat @name "你好"
    工作区:    lq chat @name --workspace /path/to/project
    """
    _run_local_chat(instance, message, project_workspace=Path(workspace) if workspace else Path.cwd())


@cli.command()
@click.argument("instance")
@click.argument("message", required=False, default="")
@click.option("--workspace", type=click.Path(exists=True, file_okay=False), default=None,
              help="项目工作区路径（默认为当前目录）")
def say(instance: str, message: str, workspace: str | None) -> None:
    """chat 的别名 — 和灵雀对话

    \b
    交互模式:  lq say @name
    单条模式:  lq say @name "你好"
    """
    _run_local_chat(instance, message, project_workspace=Path(workspace) if workspace else Path.cwd())


@cli.command()
@click.argument("instance")
@click.argument("target", type=click.Choice(["soul", "memory", "heartbeat", "config"]))
def edit(instance: str, target: str) -> None:
    """编辑实例文件"""
    home, display, _ = _resolve(instance)
    file_map = {
        "soul": home / "SOUL.md",
        "memory": home / "MEMORY.md",
        "heartbeat": home / "HEARTBEAT.md",
        "config": home / "config.json",
    }
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(file_map[target])])
