"""配置加载与管理"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

from dotenv import dotenv_values
from pypinyin import lazy_pinyin, Style

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    """将任意名称转为安全的 ASCII 目录名。

    中文 → 拼音，非 ASCII → 丢弃，空格/符号 → 连字符，全部小写。
    示例: "奶油" → "naiyu", "Test Bot" → "test-bot", "灵雀v2" → "lingquev2"
    """
    # 中文转拼音（无声调），非中文字符原样保留
    parts = lazy_pinyin(name, style=Style.NORMAL)
    ascii_name = "".join(parts)
    # 去掉剩余非 ASCII
    ascii_name = unicodedata.normalize("NFKD", ascii_name)
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii")
    # 替换空格和特殊字符为连字符
    ascii_name = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_name)
    ascii_name = ascii_name.strip("-").lower()
    return ascii_name or "lq"


@dataclass
class APIConfig:
    base_url: str = "https://api.anthropic.com"
    api_key: str = ""
    proxy: str = ""  # HTTP/SOCKS 代理（如 socks5://127.0.0.1:1080）
    mcp_key: str = ""  # 智谱 MCP API Key（联网搜索等），默认复用 api_key
    api_format: str = "anthropic"  # API 格式: "anthropic" | "openai" | "responses"
    # 透传给每次 LLM API 调用的 extra_body —— 用于供应商特有参数，比如：
    #   Qwen / Doubao / GLM-Plus: {"enable_thinking": false} 关推理省 token
    #   DeepSeek-R1 / o-series:    {"reasoning_effort": "low"}
    #   Anthropic:                 {"thinking": {"type": "enabled", "budget_tokens": 5000}}
    # 不解读不翻译，原样合并进 SDK 的 extra_body 参数。
    extra_body: dict = field(default_factory=dict)


@dataclass
class VisionConfig:
    """视觉理解模型配置（OpenAI 兼容端点，支持图片+视频）。

    留空时 vision_analyze 工具回落到 api.mcp_key 对应的智谱 GLM-4V（仅图片）。
    """
    base_url: str = ""          # e.g. https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key: str = ""           # 对应 base_url 的 API Key
    model: str = ""             # e.g. qwen3.6-plus / qwen3.6-flash / gpt-4o
    fps: float = 2.0            # 视频默认抽帧频率
    # 附加请求体字段，原样合并进 payload 顶层。用于传递厂商特有的非 OpenAI 标准参数。
    # e.g. Qwen：{"enable_thinking": false} 关闭默认思考模式
    #      GLM：{"do_sample": false}
    # 为空（默认）时不附加任何非标字段，避免严格网关 400。
    extra_params: dict = field(default_factory=dict)


@dataclass
class GroupConfig:
    chat_id: str = ""
    note: str = ""  # 群描述/用途，用于 LLM 介入判断
    eval_threshold: int = 5


@dataclass
class LQConfig:
    name: str = "lingque"       # 显示名（可以是中文）
    slug: str = ""              # 目录名（纯 ASCII），为空时自动从 name 生成
    api: APIConfig = field(default_factory=APIConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    model: str = "glm-5"
    groups: list[GroupConfig] = field(default_factory=list)
    cost_alert_daily: float = 5.0  # USD
    owner_name: str = ""  # 主人名（init 时设置）
    chat_memory_budget: int = 2000  # per-chat 长期记忆 token 预算
    autonomous_max_continuations: int = 5  # 每次心跳最多连续执行几轮自主行动
    recent_conversation_preview: int = 20  # 对话预览总条数上限
    show_thinking: bool = False  # 是否输出工具调用记录和思考过程（默认关闭，--show-thinking 开启）
    cc_max_budget_usd: float = 0.5  # Claude Code 单次执行成本上限 (USD)
    browser_port: int = 9222  # Chrome DevTools Protocol 调试端口
    enabled_tools: list[str] | None = None  # 工具白名单；None=全开，列表=只暴露列表内的工具（覆盖内置+自定义）

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = slugify(self.name)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> LQConfig:
        cfg = cls.__new__(cls)
        cfg.name = d.get("name", "lingque")
        cfg.slug = d.get("slug", "")
        cfg.model = d.get("model", "")
        cfg.cost_alert_daily = d.get("cost_alert_daily", 5.0)
        cfg.owner_name = d.get("owner_name", "")
        cfg.chat_memory_budget = d.get("chat_memory_budget", 2000)
        cfg.autonomous_max_continuations = d.get("autonomous_max_continuations", 5)
        cfg.recent_conversation_preview = d.get("recent_conversation_preview", 20)
        cfg.show_thinking = d.get("show_thinking", False)
        cfg.cc_max_budget_usd = d.get("cc_max_budget_usd", 0.5)
        cfg.browser_port = d.get("browser_port", 9222)
        et = d.get("enabled_tools")
        cfg.enabled_tools = list(et) if isinstance(et, list) else None

        api = d.get("api", {})
        api_key = api.get("api_key", "")
        cfg.api = APIConfig(
            base_url=api.get("base_url", "https://api.anthropic.com"),
            api_key=api_key,
            proxy=api.get("proxy", ""),
            mcp_key=api.get("mcp_key", "") or api_key,
            api_format=api.get("api_format", "anthropic"),
            extra_body=dict(api.get("extra_body", {}) or {}),
        )

        vis = d.get("vision", {})
        cfg.vision = VisionConfig(
            base_url=vis.get("base_url", ""),
            api_key=vis.get("api_key", ""),
            model=vis.get("model", ""),
            fps=float(vis.get("fps", 2.0)),
            extra_params=dict(vis.get("extra_params", {}) or {}),
        )

        cfg.groups = [GroupConfig(**g) for g in d.get("groups", [])]

        # 兼容旧配置：没有 slug 字段时自动生成
        if not cfg.slug:
            cfg.slug = slugify(cfg.name)
        return cfg


def resolve_home(slug: str) -> Path:
    """用 slug（纯 ASCII）构建实例主目录"""
    return Path.home() / f".lq-{slug}"


def find_instance(identifier: str) -> tuple[Path, LQConfig] | None:
    """通过 name 或 slug 查找实例。支持 `@奶油` 和 `@naiyu` 两种写法。"""
    identifier = identifier.lstrip("@")
    for entry in Path.home().iterdir():
        if not entry.is_dir() or not entry.name.startswith(".lq-"):
            continue
        config_path = entry / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = LQConfig.from_dict(json.load(f))
        except (json.JSONDecodeError, KeyError):
            continue
        if cfg.name == identifier or cfg.slug == identifier:
            return entry, cfg
    return None


def load_config(home: Path) -> LQConfig:
    config_path = home / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        return LQConfig.from_dict(json.load(f))


def save_config(home: Path, config: LQConfig) -> None:
    config_path = home / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)


def load_from_env(env_path: Path) -> LQConfig:
    """从 .env 文件读取凭证（开发模式）"""
    vals = dotenv_values(env_path)
    cfg = LQConfig()
    cfg.api.api_key = (
        vals.get("ANTHROPIC_AUTH_TOKEN")
        or vals.get("OPENAI_API_KEY")
        or vals.get("GEMINI_API_KEY")
        or ""
    )
    cfg.api.base_url = vals.get("ANTHROPIC_BASE_URL", cfg.api.base_url)
    cfg.api.api_format = vals.get("API_FORMAT", "anthropic")
    cfg.model = vals.get("MODEL", "") or cfg.model
    cfg.owner_name = vals.get("OWNER_NAME", "") or cfg.owner_name
    cfg.api.proxy = (
        vals.get("HTTPS_PROXY")
        or vals.get("HTTP_PROXY")
        or vals.get("ALL_PROXY")
        or vals.get("https_proxy")
        or vals.get("http_proxy")
        or vals.get("all_proxy")
        or ""
    )
    cfg.api.mcp_key = vals.get("ZHIPU_API_KEY", "") or cfg.api.api_key
    # API_EXTRA_BODY 是 JSON 字符串，原样合并进 SDK 的 extra_body
    raw_extra = (vals.get("API_EXTRA_BODY", "") or "").strip()
    if raw_extra:
        try:
            parsed = json.loads(raw_extra)
            if isinstance(parsed, dict):
                cfg.api.extra_body = parsed
        except json.JSONDecodeError as e:
            logger.warning("API_EXTRA_BODY JSON 解析失败，已忽略: %s", e)

    cfg.vision.base_url = vals.get("VISION_BASE_URL", "")
    cfg.vision.api_key = vals.get("VISION_API_KEY", "")
    cfg.vision.model = vals.get("VISION_MODEL", "")
    try:
        cfg.vision.fps = float(vals.get("VISION_FPS", "") or cfg.vision.fps)
    except ValueError:
        pass
    # extra_params 为厂商特有字段，建议直接在 config.json 里维护，env 不支持

    return cfg
