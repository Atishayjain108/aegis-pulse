"""
Redis Streams-backed inter-agent message bus.

Why streams (not pub/sub):
  * persistent: messages survive a consumer restart.
  * consumer groups: built-in fan-out and acknowledgments.
  * idempotent ack: pending entries can be re-delivered to a
    different consumer if one crashes.

Topology:
  * One stream per receiving agent, e.g.  agent.scout.inbox
  * One stream for the broadcast channel,  agent.broadcast
  * One consumer group per agent, e.g.    grp.scout

Every message is HMAC-signed before publish and verified after
read. Tampered or unsigned messages are dropped with a metric
increment, never delivered to handlers.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from ..schemas import AgentMessage
from .hmac_sign import sign as hmac_sign
from .hmac_sign import verify as hmac_verify

if TYPE_CHECKING:  # pragma: no cover
    from redis.asyncio import Redis as RedisClient
else:
    try:
        from redis.asyncio import Redis as RedisClient  # type: ignore
    except ImportError:  # pragma: no cover
        RedisClient = Any  # type: ignore[misc,assignment]


_log = structlog.get_logger("aegis.agents.messaging.streams")

BROADCAST_STREAM = "agent.broadcast"
INBOX_PREFIX = "agent."
INBOX_SUFFIX = ".inbox"
GROUP_PREFIX = "grp."

# Cap stream length to bound memory use on a laptop. Approximate
# trim: Redis keeps "around" maxlen, slightly more for efficiency.
_DEFAULT_MAXLEN = 10_000

Handler = Callable[[AgentMessage], Awaitable[None]]


def _inbox_for(agent: str) -> str:
    return f"{INBOX_PREFIX}{agent}{INBOX_SUFFIX}"


def _group_for(agent: str) -> str:
    return f"{GROUP_PREFIX}{agent}"


class AgentBus:
    """Producer side: publish messages to one agent or broadcast."""

    def __init__(
        self,
        redis: RedisClient,
        *,
        maxlen: int = _DEFAULT_MAXLEN,
        sign_key: bytes | None = None,
    ) -> None:
        self._redis = redis
        self._maxlen = int(maxlen)
        self._sign_key = sign_key

    async def publish(self, msg: AgentMessage) -> str | None:
        """Publish a single signed message. Returns the stream entry id,
        or None if Redis was unreachable."""
        signed = hmac_sign(msg, key=self._sign_key)
        stream = BROADCAST_STREAM if msg.to_agent == "broadcast" else _inbox_for(msg.to_agent)
        try:
            payload = signed.model_dump_json()
        except Exception:
            _log.exception("bus.serialize_failed", to_agent=msg.to_agent)
            return None
        try:
            entry_id = await self._redis.xadd(
                stream,
                {"msg": payload},
                maxlen=self._maxlen,
                approximate=True,
            )
        except Exception:
            _log.exception("bus.publish_failed", stream=stream)
            return None
        return _str(entry_id)


class BusConsumer:
    """Consumer side: blocking-read on a single agent's inbox + the
    broadcast stream, dispatch to a handler with HMAC + TTL checks."""

    def __init__(
        self,
        redis: RedisClient,
        *,
        agent: str,
        consumer_name: str | None = None,
        block_ms: int = 5_000,
        batch: int = 16,
        sign_key: bytes | None = None,
    ) -> None:
        self._redis = redis
        self.agent = agent
        self.consumer_name = consumer_name or f"{agent}-{id(self)}"
        self._block_ms = int(block_ms)
        self._batch = int(batch)
        self._sign_key = sign_key
        self._stop = asyncio.Event()
        self._streams_initialized = False

    async def _ensure_groups(self) -> None:
        """Create consumer groups idempotently. BUSYGROUP error means
        the group already exists; that's fine."""
        if self._streams_initialized:
            return
        all_ok = True
        for stream in (_inbox_for(self.agent), BROADCAST_STREAM):
            try:
                await self._redis.xgroup_create(
                    stream,
                    _group_for(self.agent),
                    id="$",  # only new messages from now on
                    mkstream=True,
                )
            except Exception as exc:
                msg = str(exc).upper()
                if "BUSYGROUP" in msg:
                    pass  # group already exists — idempotent, still OK
                else:
                    _log.exception("bus.xgroup_create_failed", stream=stream)
                    all_ok = False
        # Only mark initialized if all groups were created (or already existed).
        # Keeps retry viable if Redis was temporarily unavailable.
        if all_ok:
            self._streams_initialized = True

    async def run(self, handler: Handler) -> None:
        """Block-read loop. Stops when `stop()` is called."""
        await self._ensure_groups()
        streams = {
            _inbox_for(self.agent): ">",
            BROADCAST_STREAM: ">",
        }
        group = _group_for(self.agent)

        while not self._stop.is_set():
            try:
                resp = await self._redis.xreadgroup(
                    group,
                    self.consumer_name,
                    streams,
                    count=self._batch,
                    block=self._block_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("bus.xreadgroup_failed", agent=self.agent)
                await asyncio.sleep(1.0)
                continue

            if not resp:
                continue

            for stream_name, entries in resp:
                stream_name_s = _str(stream_name)
                for entry_id, fields in entries:
                    await self._dispatch(stream_name_s, entry_id, fields, handler, group)

    async def _dispatch(
        self,
        stream: str,
        entry_id: bytes | str,
        fields: dict[bytes, bytes] | dict[str, str],
        handler: Handler,
        group: str,
    ) -> None:
        raw = (
            fields.get(b"msg") if isinstance(next(iter(fields), b""), bytes) else fields.get("msg")
        )  # type: ignore[arg-type]
        if raw is None:
            await self._ack(stream, entry_id, group)
            return
        try:
            payload = json.loads(_str(raw))
            msg = AgentMessage.model_validate(payload)
        except Exception:
            _log.warning("bus.bad_message", stream=stream, entry_id=_str(entry_id))
            await self._ack(stream, entry_id, group)
            return

        if not hmac_verify(msg, key=self._sign_key):
            _log.warning(
                "bus.bad_signature",
                stream=stream,
                entry_id=_str(entry_id),
                from_agent=msg.from_agent,
            )
            await self._ack(stream, entry_id, group)
            return

        if msg.is_expired():
            _log.info(
                "bus.message_expired",
                stream=stream,
                ttl=msg.ttl_seconds,
                from_agent=msg.from_agent,
            )
            await self._ack(stream, entry_id, group)
            return

        try:
            await handler(msg)
        except Exception:
            _log.exception(
                "bus.handler_failed", stream=stream, agent=self.agent, from_agent=msg.from_agent
            )
            # Do NOT ack — let it be re-delivered after pending timeout.
            return
        await self._ack(stream, entry_id, group)

    async def _ack(self, stream: str, entry_id: bytes | str, group: str) -> None:
        try:
            await self._redis.xack(stream, group, entry_id)
        except Exception:
            _log.exception("bus.ack_failed", stream=stream, entry_id=_str(entry_id))

    async def stop(self) -> None:
        self._stop.set()


def _str(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
