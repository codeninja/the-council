"""MCP server – exposes The Council tools to Claude Code."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from the_council.council import Council
from the_council.defaults import DEFAULT_PERSONAS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COUNCIL_DIR_ENV = "THE_COUNCIL_DIR"
_PROJECT_ROOT_ENV = "THE_COUNCIL_PROJECT_ROOT"


def _get_council(council_dir: str | None = None, project_root: str | None = None) -> Council:
    """Return a Council instance, seeding default personas if the directory is new."""
    cd = Path(council_dir or os.environ.get(_COUNCIL_DIR_ENV, ".council"))
    pr = project_root or os.environ.get(_PROJECT_ROOT_ENV, ".")

    council = Council(council_dir=cd, project_root=pr)

    # Seed default personas on first run
    if not list((cd / "personas").glob("*.md")):
        for persona in DEFAULT_PERSONAS:
            council._persona_manager.save(persona)
            from the_council.member import CouncilMember

            council._members[persona.slug] = CouncilMember(persona, council.project_root, council.api_key)

    return council


def _ok(data: Any) -> list[TextContent]:
    if isinstance(data, str):
        return [TextContent(type="text", text=data)]
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"ERROR: {msg}")]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="council_present",
        description=(
            "Present a completed task, plan, or proposal to the council for formal review. "
            "The council will ask clarifying questions, deliberate privately, then issue a "
            "verdict (approved / rejected / modified) with full reasoning. "
            "Use this when you need approval before proceeding, or when you want a second opinion "
            "on work you have completed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "What you are presenting. State your business clearly.",
                },
                "evidence": {
                    "type": "string",
                    "description": (
                        "Supporting evidence: code snippets, diffs, test results, analysis. "
                        "Be thorough – the council will read this."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": "Optional background context about the project or task.",
                },
                "council_dir": {
                    "type": "string",
                    "description": "Optional path to the .council directory (defaults to ./.council).",
                },
                "project_root": {
                    "type": "string",
                    "description": "Optional path to the project root for file exploration.",
                },
            },
            "required": ["request"],
        },
    ),
    Tool(
        name="council_consult",
        description=(
            "Consult the council for advice when you are uncertain, stuck, or need wisdom. "
            "Each member will share their perspective based on their expertise. "
            "Use this when you need guidance rather than a formal approval."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question or problem you need advice on.",
                },
                "context": {
                    "type": "string",
                    "description": "Relevant context about what you have tried and why you are stuck.",
                },
                "council_dir": {"type": "string"},
                "project_root": {"type": "string"},
            },
            "required": ["question"],
        },
    ),
    Tool(
        name="council_create_member",
        description=(
            "Create a new council member with a custom persona. "
            "The member is immediately available for future sessions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The member's display name."},
                "title": {"type": "string", "description": "Their role or title."},
                "description": {
                    "type": "string",
                    "description": "Detailed description of their background and expertise.",
                },
                "model": {
                    "type": "string",
                    "description": "LLM model to use (default: claude-opus-4-5).",
                    "default": "claude-opus-4-5",
                },
                "traits": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of character traits.",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "Additional system instructions for this member.",
                },
                "council_dir": {"type": "string"},
                "project_root": {"type": "string"},
            },
            "required": ["name", "title", "description"],
        },
    ),
    Tool(
        name="council_list_members",
        description="List all current council members and their personas.",
        inputSchema={
            "type": "object",
            "properties": {
                "council_dir": {"type": "string"},
            },
            "required": [],
        },
    ),
    Tool(
        name="council_remove_member",
        description="Remove a council member by name or slug.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The member's name or slug."},
                "council_dir": {"type": "string"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="council_list_sessions",
        description="List past council sessions (most recent first).",
        inputSchema={
            "type": "object",
            "properties": {
                "council_dir": {"type": "string"},
            },
            "required": [],
        },
    ),
    Tool(
        name="council_get_session",
        description="Retrieve the full markdown transcript of a past council session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "The session ID."},
                "council_dir": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_server() -> Server:
    server = Server("the-council")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:  # type: ignore[return]
        return await dispatch_tool(name, arguments)

    return server


# ---------------------------------------------------------------------------
# Public dispatch function (testable without MCP plumbing)
# ---------------------------------------------------------------------------


async def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    council: Council | None = None,
) -> list[TextContent]:
    """
    Invoke a council tool by name.

    This is the implementation layer shared by the MCP server and tests.
    Pass a pre-built *council* instance to avoid filesystem side-effects in tests.
    """
    cd = arguments.get("council_dir")
    pr = arguments.get("project_root")
    c = council if council is not None else _get_council(cd, pr)

    if name == "council_present":
        try:
            session = c.present(
                request=arguments["request"],
                evidence=arguments.get("evidence", ""),
                context=arguments.get("context", ""),
            )
            summary = {
                "session_id": session.id,
                "verdict": session.verdict.value,
                "summary": session.summary_verdict(),
                "final_decision": session.final_decision,
                "questions_asked": [m.content for m in session.qa_messages],
                "public_statements": [
                    {"member": m.sender, "statement": m.content}
                    for m in session.public_deliberation
                ],
                "session_file": str(c._storage.sessions_dir / f"{session.id}.md"),
            }
            return _ok(summary)
        except Exception as exc:
            return _err(str(exc))

    if name == "council_consult":
        try:
            session = c.consult(
                question=arguments["question"],
                context=arguments.get("context", ""),
            )
            summary = {
                "session_id": session.id,
                "advice": session.final_decision,
                "questions_asked": [m.content for m in session.qa_messages],
                "session_file": str(c._storage.sessions_dir / f"{session.id}.md"),
            }
            return _ok(summary)
        except Exception as exc:
            return _err(str(exc))

    if name == "council_create_member":
        try:
            persona = c.add_member(
                name=arguments["name"],
                title=arguments["title"],
                description=arguments["description"],
                model=arguments.get("model", "claude-opus-4-5"),
                traits=arguments.get("traits", []),
                system_prompt=arguments.get("system_prompt", ""),
            )
            return _ok(
                f"Council member '{persona.name}' ({persona.slug}) created successfully. "
                f"Persona saved to .council/personas/{persona.slug}.md"
            )
        except Exception as exc:
            return _err(str(exc))

    if name == "council_list_members":
        try:
            members = c.list_members()
            if not members:
                return _ok("The council has no members yet. Use council_create_member to add some.")
            lines = ["## Current Council Members\n"]
            for m in members:
                lines.append(f"### {m['name']} (`{m['slug']}`)")
                lines.append(f"**Title:** {m['title']}  ")
                lines.append(f"**Model:** {m['model']}  ")
                lines.append(f"**Description:** {m['description'][:200]}...")
                if m["traits"]:
                    lines.append(f"**Traits:** {', '.join(m['traits'])}")
                lines.append("")
            return _ok("\n".join(lines))
        except Exception as exc:
            return _err(str(exc))

    if name == "council_remove_member":
        try:
            name_arg = arguments["name"]
            removed = c.remove_member(name_arg)
            if removed:
                return _ok(f"Council member '{name_arg}' has been removed.")
            return _err(f"No council member found with name/slug '{name_arg}'.")
        except Exception as exc:
            return _err(str(exc))

    if name == "council_list_sessions":
        try:
            sessions = c.list_sessions()
            if not sessions:
                return _ok("No council sessions found.")
            lines = ["## Council Session History\n"]
            for s in sessions:
                lines.append(
                    f"- `{s['id']}` | {s['type']} | {s['verdict']} | "
                    f"{s['created_at'][:10]} | {s['request_summary']}"
                )
            return _ok("\n".join(lines))
        except Exception as exc:
            return _err(str(exc))

    if name == "council_get_session":
        try:
            md = c.get_session(arguments["session_id"])
            if md is None:
                return _err(f"Session '{arguments['session_id']}' not found.")
            return _ok(md)
        except Exception as exc:
            return _err(str(exc))

    return _err(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    import asyncio

    asyncio.run(_run())


if __name__ == "__main__":
    main()
