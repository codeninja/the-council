"""Tests for Council orchestration with mocked AI calls."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from the_council.council import Council, _build_presentation, _tally_votes
from the_council.personas import PersonaConfig
from the_council.session import Verdict, Vote

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_council(tmp_path: Path) -> Council:
    return Council(council_dir=tmp_path / ".council", project_root=str(tmp_path))


def _mock_member(name: str, verdict: str = "approved", question: str = "") -> MagicMock:
    """Return a mock CouncilMember."""
    m = MagicMock()
    m.name = name
    m.persona = PersonaConfig(name=name, title="T", description="D")
    m.ask_questions.return_value = [question] if question else []
    m.deliberate.return_value = f"{name}'s deliberation position."
    m.vote.return_value = (verdict, f"{name} voted {verdict}.", "")
    m._build_system_prompt = MagicMock(return_value="system")
    m._call_llm = MagicMock(return_value=f"{name} public summary.")
    return m


# ---------------------------------------------------------------------------
# _build_presentation
# ---------------------------------------------------------------------------


class TestBuildPresentation:
    def test_request_only(self) -> None:
        text = _build_presentation("Do X", "", "")
        assert "Do X" in text
        assert "EVIDENCE" not in text
        assert "CONTEXT" not in text

    def test_with_evidence_and_context(self) -> None:
        text = _build_presentation("Do X", "Evidence here", "Context here")
        assert "EVIDENCE" in text
        assert "CONTEXT" in text

    def test_empty_fields_omitted(self) -> None:
        text = _build_presentation("Do X", "", "some context")
        assert "EVIDENCE" not in text
        assert "CONTEXT" in text


# ---------------------------------------------------------------------------
# _tally_votes
# ---------------------------------------------------------------------------


class TestTallyVotes:
    def test_majority_approved(self) -> None:
        votes = [
            Vote("A", Verdict.APPROVED, "good"),
            Vote("B", Verdict.APPROVED, "great"),
            Vote("C", Verdict.REJECTED, "bad"),
        ]
        verdict, text, human = _tally_votes(votes, None, MagicMock())
        assert verdict == Verdict.APPROVED
        assert not human

    def test_majority_rejected(self) -> None:
        votes = [
            Vote("A", Verdict.REJECTED, "bad"),
            Vote("B", Verdict.REJECTED, "terrible"),
            Vote("C", Verdict.APPROVED, "fine"),
        ]
        verdict, _, human = _tally_votes(votes, None, MagicMock())
        assert verdict == Verdict.REJECTED
        assert not human

    def test_deadlock_no_callback(self) -> None:
        votes = [
            Vote("A", Verdict.APPROVED, "yes"),
            Vote("B", Verdict.REJECTED, "no"),
        ]
        verdict, _, human = _tally_votes(votes, None, MagicMock())
        assert verdict == Verdict.DEADLOCKED
        assert not human

    def test_deadlock_with_human_callback_approve(self) -> None:
        votes = [
            Vote("A", Verdict.APPROVED, "yes"),
            Vote("B", Verdict.REJECTED, "no"),
        ]
        callback = MagicMock(return_value="I approve it")
        verdict, _, human = _tally_votes(votes, callback, MagicMock())
        assert verdict == Verdict.APPROVED
        assert human
        callback.assert_called_once()

    def test_deadlock_with_human_callback_reject(self) -> None:
        votes = [
            Vote("A", Verdict.APPROVED, "yes"),
            Vote("B", Verdict.REJECTED, "no"),
        ]
        callback = MagicMock(return_value="reject it please")
        verdict, _, human = _tally_votes(votes, callback, MagicMock())
        assert verdict == Verdict.REJECTED
        assert human

    def test_empty_votes(self) -> None:
        verdict, text, human = _tally_votes([], None, MagicMock())
        assert verdict == Verdict.DEADLOCKED
        assert "No votes" in text
        assert not human


# ---------------------------------------------------------------------------
# Council – member management
# ---------------------------------------------------------------------------


class TestCouncilMemberManagement:
    def test_add_member(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        persona = council.add_member(
            name="Test Elder",
            title="Test Expert",
            description="Tests everything.",
        )
        assert persona.name == "Test Elder"
        assert "test_elder" in council._members

    def test_remove_member(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        council.add_member("Elder A", "T", "D")
        assert council.remove_member("Elder A")
        assert "elder_a" not in council._members

    def test_remove_nonexistent(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        assert not council.remove_member("ghost")

    def test_list_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        council.add_member("Alpha", "T", "D")
        council.add_member("Beta", "T", "D")
        members = council.list_members()
        names = {m["name"] for m in members}
        assert names == {"Alpha", "Beta"}

    def test_get_member(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        council.add_member("Gamma", "T", "D")
        m = council.get_member("Gamma")
        assert m is not None
        assert m.name == "Gamma"

    def test_get_member_by_slug(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        council.add_member("Delta Member", "T", "D")
        m = council.get_member("delta_member")
        assert m is not None


# ---------------------------------------------------------------------------
# Council – present / consult (mocked AI)
# ---------------------------------------------------------------------------


class TestCouncilPresentation:
    def test_present_no_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        session = council.present("Should we add auth?")
        assert session.verdict == Verdict.DEADLOCKED
        assert "no members" in session.final_decision.lower()

    def test_present_with_mocked_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock_a = _mock_member("Alice", verdict="approved")
        mock_b = _mock_member("Bob", verdict="approved")
        council._members = {"alice": mock_a, "bob": mock_b}

        # Patch _summarise_position to avoid real LLM calls
        with patch("the_council.council._summarise_position", return_value="Good plan."):
            session = council.present("Implement caching layer", evidence="Benchmark results")

        assert session.verdict == Verdict.APPROVED
        assert len(session.votes) == 2
        assert session.id  # has an id

    def test_present_deadlock_triggers_human(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock_a = _mock_member("Alice", verdict="approved")
        mock_b = _mock_member("Bob", verdict="rejected")
        council._members = {"alice": mock_a, "bob": mock_b}

        human_cb = MagicMock(return_value="I approve")
        with patch("the_council.council._summarise_position", return_value="Position."):
            session = council.present("Controversial change", human_input_callback=human_cb)

        assert session.human_consulted
        assert session.verdict == Verdict.APPROVED
        human_cb.assert_called_once()

    def test_session_saved_to_disk(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock_a = _mock_member("Alice", verdict="approved")
        council._members = {"alice": mock_a}

        with patch("the_council.council._summarise_position", return_value="Ok."):
            session = council.present("Deploy to prod?")

        md = council.get_session(session.id)
        assert md is not None
        assert "Deploy to prod?" in md


class TestCouncilConsultation:
    def test_consult_no_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        session = council.consult("What should I do?")
        assert session.verdict == Verdict.ADVICE_GIVEN
        assert "no members" in session.final_decision.lower()

    def test_consult_with_mocked_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock_a = _mock_member("Alice")
        mock_b = _mock_member("Bob")
        council._members = {"alice": mock_a, "bob": mock_b}

        session = council.consult("How should I structure this API?", context="REST vs GraphQL")
        assert session.verdict == Verdict.ADVICE_GIVEN
        assert "Alice" in session.final_decision
        assert "Bob" in session.final_decision

    def test_consult_session_saved(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock_a = _mock_member("Alice")
        council._members = {"alice": mock_a}

        session = council.consult("Best approach?")
        md = council.get_session(session.id)
        assert md is not None


# ---------------------------------------------------------------------------
# Council – session history
# ---------------------------------------------------------------------------


class TestCouncilSessionHistory:
    def test_list_sessions_empty(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        assert council.list_sessions() == []

    def test_list_sessions_after_consult(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock = _mock_member("Alice")
        council._members = {"alice": mock}

        council.consult("Question one?")
        council.consult("Question two?")

        sessions = council.list_sessions()
        assert len(sessions) == 2

    def test_get_session_nonexistent(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        assert council.get_session("nonexistent_id") is None
