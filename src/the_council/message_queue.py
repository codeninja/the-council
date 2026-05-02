"""In-memory event-driven message queue with topic threading for council communication."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Message:
    """A message in the council communication system."""

    id: str
    topic: str
    sender: str
    content: str
    timestamp: datetime
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "sender": self.sender,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "parent_id": self.parent_id,
            "metadata": self.metadata,
        }


AsyncCallback = Callable[[Message], Coroutine[Any, Any, None]]


class EventQueue:
    """
    In-memory event-driven message queue for council communication.

    Supports topical threading and per-topic subscriptions.
    Messages posted to a topic are delivered to all subscribers and stored
    in history so late joiners can catch up.
    """

    def __init__(self) -> None:
        self._topics: dict[str, list[Message]] = {}
        self._subscribers: dict[str, list[AsyncCallback]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(
        self,
        topic: str,
        sender: str,
        content: str,
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Publish a message to *topic*; notify all subscribers."""
        message = Message(
            id=str(uuid.uuid4()),
            topic=topic,
            sender=sender,
            content=content,
            timestamp=datetime.now(UTC),
            parent_id=parent_id,
            metadata=metadata or {},
        )
        async with self._lock:
            self._topics.setdefault(topic, []).append(message)

        # Deliver to subscribers outside the lock (non-blocking)
        callbacks = list(self._subscribers.get(topic, []))
        for cb in callbacks:
            with contextlib.suppress(Exception):
                await cb(message)

        return message

    # ------------------------------------------------------------------
    # Subscribing
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, callback: AsyncCallback) -> None:
        """Register *callback* to be called when a message arrives on *topic*."""
        self._subscribers.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, callback: AsyncCallback) -> None:
        """Remove a previously registered callback from *topic*."""
        subs = self._subscribers.get(topic, [])
        if callback in subs:
            subs.remove(callback)

    # ------------------------------------------------------------------
    # History / inspection
    # ------------------------------------------------------------------

    def get_history(self, topic: str) -> list[Message]:
        """Return all messages posted to *topic*, oldest first."""
        return list(self._topics.get(topic, []))

    def get_thread(self, topic: str, parent_id: str) -> list[Message]:
        """Return all direct replies to *parent_id* in *topic*."""
        return [m for m in self._topics.get(topic, []) if m.parent_id == parent_id]

    def list_topics(self) -> list[str]:
        """Return all topic names that have at least one message."""
        return list(self._topics.keys())

    def clear(self) -> None:
        """Reset all topics and subscribers (useful in tests)."""
        self._topics.clear()
        self._subscribers.clear()
