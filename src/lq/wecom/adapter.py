"""企业微信 AI 机器人适配器 — 基于 WebSocket 长连接"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
import uuid

from lq.platform.adapter import PlatformAdapter
from lq.platform.types import (
    BotIdentity,
    ChatMember,
    ChatType,
    IncomingMessage,
    MessageType,
    OutgoingMessage,
    SenderType,
)

logger = logging.getLogger(__name__)

WECOM_WS_URL = "wss://openws.work.weixin.qq.com"

# 剥离消息开头的 @提及（支持多个连续 @xxx）
_AT_PREFIX = re.compile(r"^(@\S+\s*)+")

_ws_patched = False


def _patch_websockets_masking() -> None:
    """WeCom 服务端违反 RFC 6455，会发送掩码（masked）帧给客户端。
    websockets 库在 Frame.parse 里严格校验此规则并抛 ProtocolError(1002)。
    此补丁替换 Frame.parse，改为按帧头实际的 mask 位决定是否读取/应用掩码键，
    而不是按「期望值」校验，从而兼容 WeCom 的非标准行为。
    """
    global _ws_patched
    if _ws_patched:
        return
    _ws_patched = True

    try:
        from websockets.frames import Frame as _Frame
        from websockets.exceptions import ProtocolError as _PE, PayloadTooBig as _PTB

        try:
            from websockets.speedups import apply_mask as _apply_mask
        except ImportError:
            from websockets.utils import apply_mask as _apply_mask

        from websockets.frames import Opcode

        @classmethod  # type: ignore[misc]
        def _permissive_parse(cls, read_exact, *, mask, max_size=None, extensions=None):
            data = yield from read_exact(2)
            head1, head2 = struct.unpack("!BB", data)

            fin  = True if head1 & 0b10000000 else False
            rsv1 = True if head1 & 0b01000000 else False
            rsv2 = True if head1 & 0b00100000 else False
            rsv3 = True if head1 & 0b00010000 else False

            try:
                opcode = Opcode(head1 & 0b00001111)
            except ValueError as exc:
                raise _PE("invalid opcode") from exc

            frame_is_masked = True if head2 & 0b10000000 else False
            if frame_is_masked != mask:
                logger.debug(
                    "WeCom 非标准掩码帧 opcode=0x%02x masked=%s expected=%s",
                    head1 & 0x0F, frame_is_masked, mask,
                )

            length = head2 & 0b01111111
            if length == 126:
                raw = yield from read_exact(2)
                (length,) = struct.unpack("!H", raw)
            elif length == 127:
                raw = yield from read_exact(8)
                (length,) = struct.unpack("!Q", raw)

            if max_size is not None and length > max_size:
                raise _PTB(length, max_size)

            mask_bytes = None
            if frame_is_masked:
                mask_bytes = yield from read_exact(4)

            payload = yield from read_exact(length)
            if frame_is_masked and mask_bytes is not None:
                payload = _apply_mask(payload, mask_bytes)

            frame = cls(opcode, payload, fin, rsv1, rsv2, rsv3)

            for extension in reversed(extensions or []):
                frame = extension.decode(frame, max_size=max_size)

            frame.check()
            return frame

        _Frame.parse = _permissive_parse
        logger.info("WecomAdapter: 已应用 WS 掩码兼容补丁（Frame.parse）")

    except Exception:
        logger.exception("WecomAdapter: 掩码兼容补丁失败，长连接可能在 ~50s 后断开")


class WecomAdapter(PlatformAdapter):
    """企业微信 AI 机器人适配器。

    特性：
    - WebSocket 长连接，无需公网 IP
    - 凭证仅需 bot_id + secret，无扫码流程
    - 原生支持群聊（chattype=group）
    - 回复使用 Markdown 格式
    - 断线自动重连（指数退避）
    """

    def __init__(self, bot_id: str, secret: str) -> None:
        self._bot_id = bot_id
        self._secret = secret
        self._queue: asyncio.Queue | None = None
        self._ws = None
        self._tasks: list[asyncio.Task] = []
        self._shutdown = asyncio.Event()
        self._identity: BotIdentity | None = None
        self._msg_counter = 0

        # chat_id → req_id，用于将回复与来源消息关联（WeCom 协议要求透传）
        self._pending_req_ids: dict[str, str] = {}
        self._pending_req_ids_max = 500

        self._name_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # PlatformAdapter interface
    # ------------------------------------------------------------------

    async def get_identity(self) -> BotIdentity:
        if self._identity:
            return self._identity
        self._identity = BotIdentity(
            bot_id=self._bot_id,
            bot_name="",
        )
        return self._identity

    async def connect(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        self._tasks.append(
            asyncio.create_task(self._ws_loop(), name="wecom-ws")
        )
        logger.info("企业微信适配器已启动")

    async def disconnect(self) -> None:
        self._shutdown.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=3.0)
        self._tasks.clear()
        if self._ws:
            await self._ws.close()
        logger.info("企业微信适配器已停止")

    async def send(self, message: OutgoingMessage) -> str | None:
        if not self._ws:
            logger.warning("WecomAdapter: WebSocket 未连接，丢弃消息")
            return None

        req_id = self._pending_req_ids.get(message.chat_id)
        if not req_id:
            logger.warning("WecomAdapter: 无待处理 req_id，丢弃消息 chat_id=%s", message.chat_id)
            return None

        content = message.text or ""
        if message.card:
            content = self._card_to_markdown(message.card)

        payload = {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {
                "chatid": message.chat_id,  # WeCom 要求必填
                "msgtype": "markdown",
                "markdown": {"content": content},
            },
        }
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
            self._msg_counter += 1
            logger.debug("WecomAdapter: 已发送回复 chat_id=%s req_id=%s", message.chat_id, req_id)
            return f"wecom_{self._msg_counter}"
        except Exception:
            logger.exception("WecomAdapter: 消息发送失败")
            return None

    async def start_thinking(self, message_id: str) -> str | None:
        return None  # 企业微信 AI Bot 协议暂不支持 typing 状态

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        pass

    async def fetch_media(self, message_id: str, resource_key: str) -> tuple[str, str] | None:
        return None  # 暂未实现

    async def resolve_name(self, user_id: str) -> str:
        cached = self._name_cache.get(user_id)
        if cached:
            return cached
        return user_id[-8:] if len(user_id) > 8 else user_id

    async def list_members(self, chat_id: str) -> list[ChatMember]:
        return []

    # ------------------------------------------------------------------
    # Internal: WebSocket 长连接循环
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("缺少 websockets 库，请运行 uv sync")
            return

        _patch_websockets_masking()

        backoff = 3.0
        max_backoff = 60.0

        while not self._shutdown.is_set():
            try:
                logger.info("正在连接企业微信 WebSocket: %s", WECOM_WS_URL)
                async with websockets.connect(
                    WECOM_WS_URL,
                    ping_interval=None,  # 禁用自动 ping，避免 WeCom 返回掩码 pong 触发断连
                    max_size=None,
                ) as ws:
                    self._ws = ws
                    backoff = 3.0

                    await ws.send(json.dumps({
                        "cmd": "aibot_subscribe",
                        "headers": {"req_id": str(uuid.uuid4())},
                        "body": {
                            "bot_id": self._bot_id,
                            "secret": self._secret,
                        },
                    }))
                    logger.info("企业微信 WebSocket 已连接，等待消息...")

                    async for raw in ws:
                        if self._shutdown.is_set():
                            break
                        try:
                            await self._handle_frame(json.loads(raw))
                        except Exception:
                            logger.exception("WecomAdapter: 处理消息帧失败")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("WecomAdapter: 连接断开 (%s)，%.1fs 后重连", e, backoff)
            finally:
                self._ws = None

            if not self._shutdown.is_set():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def _handle_frame(self, data: dict) -> None:
        cmd = data.get("cmd")
        if cmd != "aibot_msg_callback":
            if cmd:
                logger.debug("WecomAdapter: 忽略帧 cmd=%s", cmd)
            return

        headers = data.get("headers", {})
        body = data.get("body", {})
        req_id = headers.get("req_id", str(uuid.uuid4()))

        user_id = body.get("from", {}).get("userid", "")
        chat_id = body.get("chatid", user_id)
        chattype = body.get("chattype", "single")
        msgtype = body.get("msgtype", "text")

        # 保存 req_id 以便回复时透传
        self._pending_req_ids[chat_id] = req_id
        while len(self._pending_req_ids) > self._pending_req_ids_max:
            del self._pending_req_ids[next(iter(self._pending_req_ids))]

        if msgtype != "text":
            return

        text = body.get("text", {}).get("content", "").strip()
        if not text:
            return

        is_group = chattype == "group"
        if is_group:
            text = _AT_PREFIX.sub("", text).strip()
        if not text:
            return

        self._msg_counter += 1
        msg_id = f"wecom_{self._msg_counter}"

        logger.info(
            "WecomAdapter: 收到消息 chat_id=%s user=%s text=%r",
            chat_id, user_id, text[:80],
        )

        incoming = IncomingMessage(
            message_id=msg_id,
            chat_id=chat_id,
            chat_type=ChatType.GROUP if is_group else ChatType.PRIVATE,
            sender_id=user_id,
            sender_type=SenderType.USER,
            sender_name=self._name_cache.get(
                user_id, user_id[-8:] if len(user_id) > 8 else user_id
            ),
            message_type=MessageType.TEXT,
            text=text,
            is_mention_bot=is_group,  # 群里只有 @bot 才会收到消息
            platform="wecom",
            raw=data,
        )

        if self._queue:
            self._queue.put_nowait({"event_type": "message", "message": incoming})

    @staticmethod
    def _card_to_markdown(card: dict) -> str:
        parts: list[str] = []
        title = card.get("title", "")
        content = card.get("content", "")
        if title:
            parts.append(f"**{title}**")
        if content:
            parts.append(content)
        for f in card.get("fields", []):
            key = f.get("key", "")
            value = f.get("value", "")
            if key and value:
                parts.append(f"- **{key}**: {value}")
        return "\n".join(parts) if parts else str(card)
