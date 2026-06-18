"""工具执行分发 + 多模态内容构建"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from lq.platform import IncomingMessage, OutgoingMessage
from lq.prompts import (
    RESULT_GLOBAL_MEMORY_WRITTEN, RESULT_CHAT_MEMORY_WRITTEN,
    RESULT_FILE_EMPTY, RESULT_FILE_UPDATED,
    ERR_TOOL_REGISTRY_NOT_LOADED,
    ERR_CC_NOT_LOADED, ERR_BASH_NOT_LOADED, ERR_UNKNOWN_TOOL,
    ERR_CODE_VALIDATION_OK,
)

logger = logging.getLogger(__name__)


class ToolExecMixin:
    """工具执行分发与多模态内容构建。"""

    async def _execute_tool(self, name: str, input_data: dict, chat_id: str) -> dict:
        """执行单个工具调用"""
        # 早期守卫：LLM 偶发会发出名字为空的 tool_call（schema 不合规的输出）。
        # 不进入 if/elif 链，直接返回错误，让 LLM 下一回合换有效工具名重试。
        if not name or not isinstance(name, str):
            logger.warning(
                "工具调用收到空/无效名: %r input=%s — 返回错误让 LLM 重试",
                name, json.dumps(input_data, ensure_ascii=False)[:100],
            )
            return {
                "success": False,
                "error": (
                    "tool_call had empty/invalid name — please re-emit "
                    "with a valid tool name from the available tools."
                ),
            }
        logger.info("执行工具: %s(%s)", name, json.dumps(input_data, ensure_ascii=False)[:100])

        try:
            if name == "write_memory":
                self.memory.update_memory(
                    input_data["section"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_GLOBAL_MEMORY_WRITTEN}

            elif name == "write_chat_memory":
                self.memory.update_chat_memory(
                    chat_id,
                    input_data["section"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_CHAT_MEMORY_WRITTEN}

            elif name == "read_self_file":
                content = self.memory.read_self_file(input_data["filename"])
                if not content:
                    return {"success": True, "content": RESULT_FILE_EMPTY}
                return {"success": True, "content": content}

            elif name == "write_self_file":
                self.memory.write_self_file(
                    input_data["filename"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_FILE_UPDATED.format(filename=input_data['filename'])}

            elif name == "create_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.create_tool(
                    input_data["name"],
                    input_data["code"],
                )

            elif name == "list_custom_tools":
                if not self.tool_registry:
                    return {"success": True, "tools": []}
                return {"success": True, "tools": self.tool_registry.list_tools()}

            elif name == "test_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                errors = self.tool_registry.validate_code(input_data["code"])
                if errors:
                    return {"success": False, "errors": errors}
                return {"success": True, "message": ERR_CODE_VALIDATION_OK}

            elif name == "delete_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.delete_tool(input_data["name"])

            elif name == "toggle_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.toggle_tool(
                    input_data["name"],
                    input_data["enabled"],
                )

            elif name == "run_claude_code":
                if self.cc_session:
                    # SDK 模式（优先）
                    cc_result = await self.cc_session.execute(
                        prompt=input_data["prompt"],
                        chat_id=chat_id,
                        context=self._build_cc_context(chat_id),
                        working_dir=input_data.get("working_dir", ""),
                        timeout=input_data.get("timeout", 300),
                        max_budget_usd=input_data.get("max_budget_usd", 0.5),
                        session_id=input_data.get("resume_session") or None,
                    )
                    return cc_result.to_dict()
                elif self.cc_executor:
                    # 旧模式（降级）
                    result = await self.cc_executor.execute_with_context(
                        prompt=input_data["prompt"],
                        working_dir=input_data.get("working_dir", ""),
                        timeout=input_data.get("timeout", 300),
                    )
                    return result
                else:
                    return {"success": False, "error": ERR_CC_NOT_LOADED}

            elif name == "run_bash":
                if not self.bash_executor:
                    return {"success": False, "error": ERR_BASH_NOT_LOADED}
                result = await self.bash_executor.execute(
                    command=input_data["command"],
                    working_dir=input_data.get("working_dir", ""),
                    timeout=input_data.get("timeout", 60),
                )
                return result

            elif name == "web_search":
                return await self._tool_web_search(
                    input_data["query"],
                    input_data.get("max_results", 5),
                )

            elif name == "web_fetch":
                return await self._tool_web_fetch(
                    input_data["url"],
                    input_data.get("max_length", 8000),
                )

            elif name == "run_python":
                return await self._tool_run_python(
                    input_data["code"],
                    input_data.get("timeout", 30),
                )

            elif name == "read_file":
                return self._tool_read_file(
                    input_data["path"],
                    input_data.get("max_lines", 500),
                )

            elif name == "write_file":
                return self._tool_write_file(
                    input_data["path"],
                    input_data["content"],
                )

            elif name == "edit_file":
                return self._tool_edit_file(
                    input_data["path"],
                    input_data["old_string"],
                    input_data["new_string"],
                )

            elif name == "get_my_stats":
                return self._tool_get_my_stats(
                    input_data.get("category", "today"),
                )

            elif name == "detect_drift":
                return self._tool_detect_drift(
                    input_data.get("days", 1),
                )

            elif name == "browser_action":
                return await self._tool_browser_action(input_data)

            elif name == "vision_analyze":
                fps_raw = input_data.get("fps")
                return await self._tool_vision_analyze(
                    image_source=input_data.get("image_source", ""),
                    prompt=input_data.get("prompt", "描述这张图片的内容"),
                    video_source=input_data.get("video_source", ""),
                    fps=float(fps_raw) if fps_raw is not None else None,
                )

            else:
                # 尝试自定义工具注册表
                if self.tool_registry and self.tool_registry.has_tool(name):
                    import httpx

                    # 构建可调用函数包装，供自定义工具使用
                    async def _ctx_run_bash(command: str, working_dir: str = "", timeout: int = 60) -> dict:
                        if not self.bash_executor:
                            return {"success": False, "error": "Bash executor not loaded"}
                        return await self.bash_executor.execute(command=command, working_dir=working_dir, timeout=timeout)

                    async def _ctx_run_claude_code(prompt: str, working_dir: str = "", timeout: int = 300, max_budget_usd: float = 0.5, resume_session: str = "") -> dict:
                        if self.cc_session:
                            cc_result = await self.cc_session.execute(
                                prompt=prompt, chat_id=chat_id, context=self._build_cc_context(chat_id),
                                working_dir=working_dir, timeout=timeout, max_budget_usd=max_budget_usd,
                                session_id=resume_session or None,
                            )
                            return cc_result.to_dict()
                        elif self.cc_executor:
                            return await self.cc_executor.execute_with_context(prompt=prompt, working_dir=working_dir, timeout=timeout)
                        return {"success": False, "error": "Claude Code executor not loaded"}

                    async def _ctx_read_self_file(filename: str) -> dict:
                        content = self.memory.read_self_file(filename)
                        return {"success": True, "content": content or ""}

                    async def _ctx_write_self_file(filename: str, content: str) -> dict:
                        self.memory.write_self_file(filename, content)
                        return {"success": True, "message": f"File {filename} updated"}

                    async def _ctx_write_memory(section: str, content: str) -> dict:
                        self.memory.update_memory(section, content)
                        return {"success": True, "message": "Memory updated"}

                    async def _ctx_write_chat_memory(chat_id: str, section: str, content: str) -> dict:
                        self.memory.update_chat_memory(chat_id, section, content)
                        return {"success": True, "message": "Chat memory updated"}

                    async with httpx.AsyncClient() as http_client:
                        context = {
                            "sender": "system",
                            "chat_id": chat_id,
                            "adapter": self.adapter,
                            "memory": self.memory,
                            "calendar": None,
                            "session_mgr": self.session_mgr,
                            "http": http_client,
                            "run_bash": _ctx_run_bash,
                            "run_claude_code": _ctx_run_claude_code,
                            "read_self_file": _ctx_read_self_file,
                            "write_self_file": _ctx_write_self_file,
                            "write_memory": _ctx_write_memory,
                            "write_chat_memory": _ctx_write_chat_memory,
                        }
                        return await self.tool_registry.execute(name, input_data, context)
                return {"success": False, "error": ERR_UNKNOWN_TOOL.format(name=name)}

        except Exception as e:
            logger.exception("工具执行失败: %s", name)
            return {"success": False, "error": str(e)}

    def _build_cc_context(self, chat_id: str) -> str:
        """构建 CC 执行的对话上下文摘要"""
        if not self.session_mgr:
            return ""
        try:
            session = self.session_mgr.get_or_create(chat_id)
            messages = session.get_messages()
            if not messages:
                return ""
            # 取最近 5 条消息作为上下文
            recent = messages[-5:]
            lines: list[str] = []
            for m in recent:
                role = m.get("role", "?")
                content = m.get("content", "")
                if isinstance(content, str) and content:
                    lines.append(f"{role}: {content[:200]}")
            return "\n".join(lines)
        except Exception:
            return ""

    # ── 多模态内容构建 ──

    async def _build_multimodal_content(
        self, msg: IncomingMessage, text: str,
    ) -> str | list[dict]:
        """构建多模态内容：如果消息含图片则返回 content blocks 列表，否则返回纯文本。

        返回格式兼容 Anthropic Messages API：
        - 纯文本: "hello"
        - 多模态: [{"type": "image", "source": {...}}, {"type": "text", "text": "hello"}]

        图片下载失败时会在文本中附带提示，让 LLM 知道有图片未能加载。
        """
        if not msg.image_keys:
            return text

        blocks: list[dict] = []
        failed_count = 0

        for key in msg.image_keys:
            result = await self.adapter.fetch_media(msg.message_id, key)
            if result:
                b64_data, media_type = result
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                })
            else:
                failed_count += 1

        # 构建文本部分，附带下载失败提示
        text_parts = []
        if text:
            text_parts.append(text)
        if failed_count:
            text_parts.append(f"（有 {failed_count} 张图片加载失败，无法查看）")

        text_combined = "\n".join(text_parts) if text_parts else ""

        if text_combined:
            blocks.append({"type": "text", "text": text_combined})
        elif not blocks:
            # 无图片也无文本
            return ""
        elif not any(b["type"] == "text" for b in blocks):
            # 有图片但没文本，加个默认提示
            blocks.append({"type": "text", "text": "（用户发送了图片）"})

        return blocks

    async def _build_image_content(
        self, image_messages: list[IncomingMessage], text: str,
    ) -> str | list[dict]:
        """从多条图片消息中下载图片，与文本合并为 content blocks。

        用于防抖合并场景：多条消息（可能混合文本和图片）合并后统一处理。
        图片下载失败时会在文本中附带提示。
        """
        blocks: list[dict] = []
        failed_count = 0
        for msg in image_messages:
            for key in msg.image_keys:
                result = await self.adapter.fetch_media(msg.message_id, key)
                if result:
                    b64_data, media_type = result
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    })
                else:
                    failed_count += 1

        text_parts = []
        if text:
            text_parts.append(text)
        if failed_count:
            text_parts.append(f"（有 {failed_count} 张图片加载失败，无法查看）")

        text_combined = "\n".join(text_parts) if text_parts else ""

        if text_combined:
            blocks.append({"type": "text", "text": text_combined})
        elif not blocks:
            return ""
        elif not any(b.get("type") == "text" for b in blocks):
            blocks.append({"type": "text", "text": "（用户发送了图片）"})

        return blocks
