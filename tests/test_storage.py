"""Tests for Storage – session persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from the_council.message_queue import Message
from the_council.session import CouncilSession, SessionType, Verdict, Vote
from the_council.storage import Storage


def _make_session(session_id: str = "abc12345") -> CouncilSession:
    s = CouncilSession(
        id=session_id,
        session_type=SessionType.PRESENTATION,
        created_at=datetime(2024, 1, 15, 12, 0, 0),
        request="Should we refactor the auth module?",
        evidence="Current code has 3 CVEs.",
        context="Auth module was written in 2019.",
        participants=["Alice", "Bob"],
    )
    s.votes = [
        Vote("Alice", Verdict.APPROVED, "Makes sense.", ""),
        Vote("Bob", Verdict.MODIFIED, "Needs tests first.", "Add 90% coverage before merge."),
    ]
    s.verdict = Verdict.MODIFIED
    s.final_decision = "Approved with conditions: add test coverage."
    return s


class TestStorage:
    @pytest.fixture
    def storage(self, tmp_path: Path) -> Storage:
        return Storage(tmp_path / "council")

    def test_save_and_load_markdown(self, storage: Storage) -> None:
        session = _make_session("test0001")
        storage.save_session(session)
        md = storage.load_session_markdown("test0001")
        assert md is not None
        assert "test0001" in md
        assert "Should we refactor" in md
        assert "MODIFIED" in md

    def test_load_nonexistent_session(self, storage: Storage) -> None:
        assert storage.load_session_markdown("nope9999") is None

    def test_sessions_dir_created(self, tmp_path: Path) -> None:
        Storage(tmp_path / "new_council")
        assert (tmp_path / "new_council" / "sessions").exists()

    def test_list_sessions_empty(self, storage: Storage) -> None:
        assert storage.list_sessions() == []

    def test_list_sessions_after_save(self, storage: Storage) -> None:
        s1 = _make_session("s001")
        s2 = _make_session("s002")
        s2.session_type = SessionType.CONSULTATION
        s2.verdict = Verdict.ADVICE_GIVEN
        storage.save_session(s1)
        storage.save_session(s2)
        sessions = storage.list_sessions()
        assert len(sessions) == 2
        # most recent first
        assert sessions[0]["id"] == "s002"
        assert sessions[1]["id"] == "s001"

    def test_save_updates_existing_index_entry(self, storage: Storage) -> None:
        s = _make_session("upd0001")
        storage.save_session(s)
        s.final_decision = "Updated decision"
        storage.save_session(s)
        sessions = storage.list_sessions()
        # Should not duplicate
        ids = [x["id"] for x in sessions]
        assert ids.count("upd0001") == 1

    def test_json_file_created(self, storage: Storage) -> None:
        session = _make_session("js000001")
        storage.save_session(session)
        json_path = storage.sessions_dir / "js000001.json"
        assert json_path.exists()

    def test_markdown_contains_votes(self, storage: Storage) -> None:
        session = _make_session("vote001")
        storage.save_session(session)
        md = storage.load_session_markdown("vote001")
        assert md is not None
        assert "Alice" in md
        assert "Bob" in md
        assert "approved" in md.lower()
        assert "modified" in md.lower()


class TestSessionMarkdown:
    def test_to_markdown_structure(self) -> None:
        s = _make_session()
        md = s.to_markdown()
        assert "# Council Session" in md
        assert "## Request" in md
        assert "## Votes" in md
        assert "## Final Decision" in md

    def test_summary_verdict(self) -> None:
        s = _make_session()
        summary = s.summary_verdict()
        assert "modified" in summary.lower() or "approved" in summary.lower()

    def test_human_consulted_flag(self) -> None:
        s = _make_session()
        s.human_consulted = True
        md = s.to_markdown()
        assert "Human was consulted" in md

    def test_qa_messages_in_markdown(self) -> None:
        s = _make_session()
        s.qa_messages = [
            Message(
                id="m1",
                topic="t",
                sender="Alice",
                content="What about backward compat?",
                timestamp=datetime.now(UTC),
            )
        ]
        md = s.to_markdown()
        assert "What about backward compat?" in md
