"""Tests for the in-memory event-driven message queue."""

from __future__ import annotations

import pytest

from the_council.message_queue import EventQueue, Message


class TestEventQueue:
    @pytest.fixture
    def queue(self) -> EventQueue:
        return EventQueue()

    # ------------------------------------------------------------------
    # publish / history
    # ------------------------------------------------------------------

    async def test_publish_stores_message(self, queue: EventQueue) -> None:
        msg = await queue.publish("topic:a", "Alice", "Hello")
        assert isinstance(msg, Message)
        assert msg.topic == "topic:a"
        assert msg.sender == "Alice"
        assert msg.content == "Hello"
        assert msg.id  # non-empty uuid

    async def test_get_history_returns_messages_in_order(self, queue: EventQueue) -> None:
        await queue.publish("topic:a", "Alice", "first")
        await queue.publish("topic:a", "Bob", "second")
        history = queue.get_history("topic:a")
        assert len(history) == 2
        assert history[0].content == "first"
        assert history[1].content == "second"

    async def test_get_history_empty_topic(self, queue: EventQueue) -> None:
        assert queue.get_history("no-such-topic") == []

    async def test_multiple_topics_isolated(self, queue: EventQueue) -> None:
        await queue.publish("topic:a", "Alice", "msg-a")
        await queue.publish("topic:b", "Bob", "msg-b")
        assert len(queue.get_history("topic:a")) == 1
        assert len(queue.get_history("topic:b")) == 1

    # ------------------------------------------------------------------
    # subscribe / notify
    # ------------------------------------------------------------------

    async def test_subscriber_receives_published_messages(self, queue: EventQueue) -> None:
        received: list[Message] = []

        async def on_msg(m: Message) -> None:
            received.append(m)

        queue.subscribe("topic:a", on_msg)
        await queue.publish("topic:a", "Alice", "Hello")
        await queue.publish("topic:a", "Bob", "World")
        assert len(received) == 2
        assert received[0].sender == "Alice"
        assert received[1].sender == "Bob"

    async def test_subscriber_only_gets_its_topic(self, queue: EventQueue) -> None:
        received: list[Message] = []

        async def on_msg(m: Message) -> None:
            received.append(m)

        queue.subscribe("topic:a", on_msg)
        await queue.publish("topic:b", "Bob", "should not arrive")
        assert received == []

    async def test_unsubscribe_stops_delivery(self, queue: EventQueue) -> None:
        received: list[Message] = []

        async def on_msg(m: Message) -> None:
            received.append(m)

        queue.subscribe("topic:a", on_msg)
        await queue.publish("topic:a", "Alice", "first")
        queue.unsubscribe("topic:a", on_msg)
        await queue.publish("topic:a", "Alice", "second")
        assert len(received) == 1

    async def test_bad_subscriber_does_not_crash_queue(self, queue: EventQueue) -> None:
        async def bad_cb(m: Message) -> None:
            raise RuntimeError("boom")

        queue.subscribe("topic:a", bad_cb)
        # Should not raise
        msg = await queue.publish("topic:a", "Alice", "Hello")
        assert msg.content == "Hello"

    # ------------------------------------------------------------------
    # threading (parent_id)
    # ------------------------------------------------------------------

    async def test_get_thread_returns_replies(self, queue: EventQueue) -> None:
        root = await queue.publish("topic:a", "Alice", "root question")
        await queue.publish("topic:a", "Bob", "reply 1", parent_id=root.id)
        await queue.publish("topic:a", "Carol", "reply 2", parent_id=root.id)
        await queue.publish("topic:a", "Dave", "unrelated")

        thread = queue.get_thread("topic:a", root.id)
        assert len(thread) == 2
        assert {m.sender for m in thread} == {"Bob", "Carol"}

    # ------------------------------------------------------------------
    # list_topics / clear
    # ------------------------------------------------------------------

    async def test_list_topics(self, queue: EventQueue) -> None:
        await queue.publish("alpha", "A", "x")
        await queue.publish("beta", "B", "y")
        topics = queue.list_topics()
        assert set(topics) == {"alpha", "beta"}

    async def test_clear_resets_state(self, queue: EventQueue) -> None:
        await queue.publish("alpha", "A", "x")
        queue.clear()
        assert queue.list_topics() == []

    # ------------------------------------------------------------------
    # metadata
    # ------------------------------------------------------------------

    async def test_metadata_stored(self, queue: EventQueue) -> None:
        await queue.publish("t", "A", "hi", metadata={"priority": "high"})
        history = queue.get_history("t")
        assert history[0].metadata["priority"] == "high"

    # ------------------------------------------------------------------
    # to_dict
    # ------------------------------------------------------------------

    async def test_message_to_dict(self, queue: EventQueue) -> None:
        msg = await queue.publish("t", "A", "hello")
        d = msg.to_dict()
        assert d["sender"] == "A"
        assert d["content"] == "hello"
        assert "timestamp" in d
        assert d["topic"] == "t"
