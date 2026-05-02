"""Tests for the MCP server dispatch layer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from the_council.council import Council
from the_council.main import TOOLS, _err, _ok, dispatch_tool
from the_council.personas import PersonaConfig


def _make_council(tmp_path: Path) -> Council:
    return Council(council_dir=tmp_path / ".council", project_root=str(tmp_path))


def _mock_member(name: str, verdict: str = "approved") -> MagicMock:
    m = MagicMock()
    m.name = name
    m.persona = PersonaConfig(name=name, title="T", description="D")
    m.ask_questions.return_value = []
    m.deliberate.return_value = f"{name}'s position."
    m.vote.return_value = (verdict, f"Because {name}.", "")
    m._build_system_prompt = MagicMock(return_value="")
    m._call_llm = MagicMock(return_value="Public statement.")
    return m


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_ok_string(self) -> None:
        result = _ok("hello")
        assert len(result) == 1
        assert result[0].text == "hello"

    def test_ok_dict(self) -> None:
        result = _ok({"key": "value"})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["key"] == "value"

    def test_err(self) -> None:
        result = _err("something went wrong")
        assert "ERROR" in result[0].text
        assert "something went wrong" in result[0].text


# ---------------------------------------------------------------------------
# TOOLS list
# ---------------------------------------------------------------------------


class TestToolsDefinition:
    def test_all_tools_present(self) -> None:
        tool_names = {t.name for t in TOOLS}
        expected = {
            "council_present",
            "council_consult",
            "council_create_member",
            "council_list_members",
            "council_remove_member",
            "council_list_sessions",
            "council_get_session",
        }
        assert expected == tool_names

    def test_all_tools_have_descriptions(self) -> None:
        for tool in TOOLS:
            assert tool.description, f"{tool.name} has no description"

    def test_all_tools_have_input_schema(self) -> None:
        for tool in TOOLS:
            assert tool.inputSchema, f"{tool.name} has no inputSchema"


# ---------------------------------------------------------------------------
# dispatch_tool tests
# ---------------------------------------------------------------------------


class TestDispatchCreateMember:
    async def test_create_member(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool(
            "council_create_member",
            {"name": "New Elder", "title": "Test Expert", "description": "An AI elder for testing."},
            council=council,
        )
        assert "New Elder" in result[0].text
        assert "created" in result[0].text.lower()

    async def test_create_member_with_traits(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool(
            "council_create_member",
            {
                "name": "Trait Elder",
                "title": "T",
                "description": "D",
                "traits": ["curious", "direct"],
            },
            council=council,
        )
        assert "Trait Elder" in result[0].text


class TestDispatchListMembers:
    async def test_list_members_empty(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool("council_list_members", {}, council=council)
        assert "no members" in result[0].text.lower()

    async def test_list_members_with_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        council.add_member("Elder One", "T", "D")
        result = await dispatch_tool("council_list_members", {}, council=council)
        assert "Elder One" in result[0].text


class TestDispatchRemoveMember:
    async def test_remove_member(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        council.add_member("Removable", "T", "D")
        result = await dispatch_tool("council_remove_member", {"name": "Removable"}, council=council)
        assert "removed" in result[0].text.lower()

    async def test_remove_nonexistent_member(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool("council_remove_member", {"name": "ghost"}, council=council)
        assert "ERROR" in result[0].text


class TestDispatchListSessions:
    async def test_list_sessions_empty(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool("council_list_sessions", {}, council=council)
        assert "No council sessions" in result[0].text

    async def test_list_sessions_after_consult(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock = _mock_member("Elder")
        council._members = {"elder": mock}
        council.consult("What to do?")
        result = await dispatch_tool("council_list_sessions", {}, council=council)
        assert "consultation" in result[0].text.lower()


class TestDispatchGetSession:
    async def test_get_session_not_found(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool(
            "council_get_session", {"session_id": "nonexistent"}, council=council
        )
        assert "ERROR" in result[0].text

    async def test_get_session_after_consult(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock = _mock_member("Elder")
        council._members = {"elder": mock}
        session = council.consult("How to test?")
        result = await dispatch_tool(
            "council_get_session", {"session_id": session.id}, council=council
        )
        assert "How to test?" in result[0].text


class TestDispatchPresent:
    async def test_present_no_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool(
            "council_present", {"request": "Should we deploy?"}, council=council
        )
        data = json.loads(result[0].text)
        assert "session_id" in data
        assert data["verdict"] == "deadlocked"

    async def test_present_with_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock = _mock_member("TestElder", verdict="approved")
        council._members = {"testeleder": mock}
        with patch("the_council.council._summarise_position", return_value="Looks good."):
            result = await dispatch_tool(
                "council_present",
                {"request": "Should we refactor?", "evidence": "Old code."},
                council=council,
            )
        data = json.loads(result[0].text)
        assert data["verdict"] == "approved"

    async def test_present_saves_session(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock = _mock_member("Alice", verdict="approved")
        council._members = {"alice": mock}
        with patch("the_council.council._summarise_position", return_value="Ok."):
            result = await dispatch_tool(
                "council_present", {"request": "Deploy to prod?"}, council=council
            )
        data = json.loads(result[0].text)
        session_md = council.get_session(data["session_id"])
        assert session_md is not None
        assert "Deploy to prod?" in session_md


class TestDispatchConsult:
    async def test_consult_no_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool(
            "council_consult", {"question": "Best architecture?"}, council=council
        )
        data = json.loads(result[0].text)
        assert "session_id" in data

    async def test_consult_with_members(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        mock = _mock_member("Elder")
        council._members = {"elder": mock}
        result = await dispatch_tool(
            "council_consult",
            {"question": "How to structure this?", "context": "REST vs GraphQL"},
            council=council,
        )
        data = json.loads(result[0].text)
        assert "advice" in data


class TestDispatchUnknownTool:
    async def test_unknown_tool_returns_error(self, tmp_path: Path) -> None:
        council = _make_council(tmp_path)
        result = await dispatch_tool("unknown_tool_xyz", {}, council=council)
        assert "ERROR" in result[0].text
