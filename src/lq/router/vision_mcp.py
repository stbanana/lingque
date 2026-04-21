"""Vision 工具：根据 config.vision 调用任意 OpenAI 兼容的视觉理解模型。

路由规则（由配置决定，不做字符串嗅探）：
- config.vision 三要素（base_url + api_key + model）齐全 → 走 OpenAI 兼容端点
  （适配 Qwen / GLM / GPT / Gemini 等一切兼容实现）
- 否则 → 回落到 executor.mcp_key 对应的智谱 GLM-4V（历史行为，仅图片）
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ─── MIME 推断 ───

_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "heic": "image/heic",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}

_VIDEO_MIME = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "m4v": "video/x-m4v",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "flv": "video/x-flv",
    "wmv": "video/x-ms-wmv",
}


def _source_to_url(source: str, kind: str) -> str:
    """将 image/video source（URL / data URI / 本地路径）统一转成可直传的 URL 或 data URI。

    kind: "image" 或 "video"
    """
    if source.startswith(("http://", "https://", "data:")):
        return source

    path = Path(source)
    if path.is_file():
        raw = path.read_bytes()
        ext = path.suffix.lstrip(".").lower()
        mime_table = _IMAGE_MIME if kind == "image" else _VIDEO_MIME
        default_mime = "image/png" if kind == "image" else "video/mp4"
        mime = mime_table.get(ext, default_mime)
        size_mb = len(raw) / (1024 * 1024)
        if size_mb > 10:
            logger.warning("%s base64 过大: %.1fMB（部分 API 上限 10MB）", kind, size_mb)
        b64 = base64.b64encode(raw).decode()
        return f"data:{mime};base64,{b64}"

    # fallback：作为裸 base64 字符串对待
    fallback_mime = "image/jpeg" if kind == "image" else "video/mp4"
    return f"data:{fallback_mime};base64,{source}"


# ─── OpenAI 兼容视觉端点（Qwen / GLM-Vision / GPT-4o / Gemini 等） ───

async def _call_openai_compatible_vision(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_source: str | None,
    video_source: str | None,
    fps: float,
    extra_params: dict | None = None,
) -> dict:
    """通过 OpenAI 兼容接口调用视觉模型。

    base_url: 应为 OpenAI 兼容的根路径，函数会拼接 "/chat/completions"。
              e.g. https://dashscope.aliyuncs.com/compatible-mode/v1
    extra_params: 原样合并进 payload 顶层，用于传递厂商特有字段（如 Qwen 的 enable_thinking）。
    """
    content: list[dict[str, Any]] = []

    if video_source:
        content.append({
            "type": "video_url",
            "video_url": {"url": _source_to_url(video_source, "video")},
            "fps": fps,
        })
    elif image_source:
        content.append({
            "type": "image_url",
            "image_url": {"url": _source_to_url(image_source, "image")},
        })
    else:
        return {"success": False, "error": "image_source 与 video_source 至少需提供一个"}

    content.append({"type": "text", "text": prompt})

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }
    if extra_params:
        # 用户可用 extra_params 覆盖 model/messages 也允许（"trust the user"）
        payload.update(extra_params)

    endpoint = base_url.rstrip("/") + "/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception as e:
        logger.exception("Vision 请求异常 (endpoint=%s)", endpoint)
        return {"success": False, "error": f"请求异常: {e}"}

    if resp.status_code != 200:
        return {"success": False, "error": f"API 请求失败: {resp.status_code} - {resp.text[:500]}"}

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        return {"success": False, "error": "API 未返回结果"}

    analysis = choices[0].get("message", {}).get("content", "") or "（未返回分析结果）"
    usage = data.get("usage", {}) or {}
    return {
        "success": True,
        "analysis": analysis,
        "engine": model,
        "media_kind": "video" if video_source else "image",
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
    }


# ─── 智谱 GLM-4V 兜底（未配置 config.vision 时使用，仅图片） ───

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


async def _call_zhipu_vision(api_key: str, prompt: str, image_source: str) -> dict:
    image_url = _source_to_url(image_source, "image")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                ZHIPU_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "glm-4v-flash",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_url}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                    "max_tokens": 1024,
                },
            )
    except Exception as e:
        logger.exception("Zhipu vision 请求异常")
        return {"success": False, "error": f"请求异常: {e}"}

    if resp.status_code != 200:
        return {"success": False, "error": f"API 请求失败: {resp.status_code} - {resp.text[:500]}"}

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        return {"success": False, "error": "API 未返回结果"}

    analysis = choices[0].get("message", {}).get("content", "") or "（未返回分析结果）"
    return {
        "success": True,
        "analysis": analysis,
        "engine": "zhipu:glm-4v-flash",
        "media_kind": "image",
    }


# ─── Mixin ───

class VisionMCPMixin:
    """视觉分析能力。优先读取 config.vision，未配置时回落到 GLM-4V。"""

    async def _tool_vision_analyze(
        self,
        image_source: str = "",
        prompt: str = "描述这张图片的内容",
        video_source: str = "",
        fps: float | None = None,
    ) -> dict:
        """分析图片或视频。

        参数路由：
          - video_source 非空 → 视频分析（需 config.vision 已配置）
          - 否则使用 image_source 做图片分析
        """
        source_display = video_source or image_source or "(empty)"

        vision = getattr(getattr(self, "config", None), "vision", None)

        # 主路径：config.vision 三要素齐全 → OpenAI 兼容端点
        if vision and vision.base_url and vision.api_key and vision.model:
            effective_fps = fps if fps is not None else vision.fps
            result = await _call_openai_compatible_vision(
                base_url=vision.base_url,
                api_key=vision.api_key,
                model=vision.model,
                prompt=prompt,
                image_source=image_source or None,
                video_source=video_source or None,
                fps=effective_fps,
                extra_params=vision.extra_params,
            )
            result.setdefault("source", source_display)
            return result

        # 回落：智谱 GLM-4V（历史行为，仅图片）
        if video_source:
            return {
                "success": False,
                "error": "未配置 config.vision，当前回落到 GLM-4V 不支持视频。请在 config.json 中添加 vision 配置。",
            }

        zhipu_key = (
            getattr(self.executor, "mcp_key", "")
            or os.environ.get("Z_AI_API_KEY", "")
        )
        if not zhipu_key:
            return {"success": False, "error": "未配置 config.vision，也未找到 mcp_key / Z_AI_API_KEY"}
        if not image_source:
            return {"success": False, "error": "image_source 为空"}

        result = await _call_zhipu_vision(zhipu_key, prompt, image_source)
        result.setdefault("source", source_display)
        return result
