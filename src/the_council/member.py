"""Council member AI agent – one AI-backed persona on the council."""

from __future__ import annotations

import os
from typing import Any

from the_council.backends import LLMBackend, make_backend
from the_council.message_queue import EventQueue
from the_council.personas import PersonaConfig

# ---------------------------------------------------------------------------
# File-exploration tool implementation
# ---------------------------------------------------------------------------


def _handle_tool_call(name: str, tool_input: dict[str, Any], project_root: str) -> str:
    """Execute a tool call from a council member and return the result as text."""
    if name == "read_file":
        path = os.path.join(project_root, tool_input.get("path", ""))
        # Security: prevent path traversal outside project root
        real_root = os.path.realpath(project_root)
        real_path = os.path.realpath(path)
        if not real_path.startswith(real_root):
            return "Error: access outside project root is not permitted."
        if not os.path.isfile(real_path):
            return f"Error: file not found: {tool_input.get('path')}"
        try:
            with open(real_path, encoding="utf-8", errors="replace") as fh:
                content = fh.read(32_768)  # cap at ~32 KB
            return content
        except OSError as exc:
            return f"Error reading file: {exc}"

    if name == "list_files":
        dir_path = os.path.join(project_root, tool_input.get("path", "."))
        real_root = os.path.realpath(project_root)
        real_path = os.path.realpath(dir_path)
        if not real_path.startswith(real_root):
            return "Error: access outside project root is not permitted."
        if not os.path.isdir(real_path):
            return f"Error: directory not found: {tool_input.get('path', '.')}"
        try:
            entries = sorted(os.listdir(real_path))
            return "\n".join(entries) if entries else "(empty directory)"
        except OSError as exc:
            return f"Error listing directory: {exc}"

    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# CouncilMember
# ---------------------------------------------------------------------------


class CouncilMember:
    """
    A council member backed by an LLM via a pluggable :class:`~the_council.backends.LLMBackend`.

    The backend is selected automatically from the persona's ``provider`` field,
    enabling Anthropic, OpenAI, OpenRouter, Ollama, and any other OpenAI-compatible
    endpoint to be used on a per-member basis.

    Each member has a persona that shapes how they respond.  They communicate
    with other members via the shared :class:`~the_council.message_queue.EventQueue`.
    """

    def __init__(
        self,
        persona: PersonaConfig,
        project_root: str = ".",
        api_key: str | None = None,
        *,
        backend: LLMBackend | None = None,
    ) -> None:
        """
        Parameters
        ----------
        persona:
            The persona configuration for this member.
        project_root:
            Absolute path to the project root used for file-exploration tools.
        api_key:
            Optional global API key override (used only when the persona does not
            carry its own ``api_key`` and no provider env var is set).  Kept for
            backward compatibility; prefer setting ``persona.api_key`` or the
            appropriate environment variable instead.
        backend:
            Inject a pre-built :class:`LLMBackend` directly (useful for testing).
            When provided, *api_key*, ``persona.provider``, and ``persona.model``
            are ignored for backend construction.
        """
        self.persona = persona
        self.project_root = os.path.abspath(project_root)

        if backend is not None:
            self._backend: LLMBackend = backend
        else:
            # Per-persona key wins; fall back to the explicit api_key argument
            # (legacy) only when the persona carries no key of its own.
            resolved_key = persona.api_key or api_key or None
            self._backend = make_backend(
                provider=persona.provider,
                model=persona.model,
                api_key=resolved_key,
            )

    @property
    def name(self) -> str:
        return self.persona.name

    # ------------------------------------------------------------------
    # Core reasoning method
    # ------------------------------------------------------------------

    def _call_llm(self, system: str, messages: list[dict[str, Any]]) -> str:
        """
        Call the configured backend with an agentic tool-use loop.
        Returns the final text response.
        """

        def _tool_handler(name: str, tool_input: dict[str, Any]) -> str:
            return _handle_tool_call(name, tool_input, self.project_root)

        return self._backend.complete(system, messages, _tool_handler)

    # ------------------------------------------------------------------
    # Council-facing methods
    # ------------------------------------------------------------------

    def _build_system_prompt(self, extra: str = "") -> str:
        base = f"""You are {self.persona.name}, {self.persona.title}.

{self.persona.description}

{"Traits: " + ", ".join(self.persona.traits) if self.persona.traits else ""}

{self.persona.system_prompt}

You are a member of an AI Elder Council. Your role is to provide wise, thoughtful guidance
based on your unique perspective and expertise. Be direct, specific, and constructive.
Keep responses focused and under 400 words unless depth is clearly warranted.
""".strip()
        if extra:
            base = base + "\n\n" + extra
        return base

    def ask_questions(self, presentation: str) -> list[str]:
        """
        Given a presentation, return a list of clarifying questions (0-3).
        Returns an empty list if no questions are needed.
        """
        system = self._build_system_prompt(
            "Ask 0-3 sharp, specific clarifying questions about the presentation. "
            "Return ONLY a newline-separated list of questions, or an empty response if none."
        )
        user_msg = f"PRESENTATION:\n\n{presentation}"
        raw = self._call_llm(system, [{"role": "user", "content": user_msg}])
        questions = [q.strip().lstrip("0123456789.-) ") for q in raw.splitlines() if q.strip() and "?" in q]
        return questions[:3]

    def deliberate(
        self,
        presentation: str,
        qa_transcript: str,
        peer_messages: list[str],
        queue: EventQueue,
        deliberation_topic: str,
    ) -> str:
        """
        Deliberate privately and return this member's position statement.
        Also posts it to the private deliberation topic on the queue.
        """
        peer_context = "\n".join(peer_messages) if peer_messages else "No peer messages yet."
        system = self._build_system_prompt(
            "You are in PRIVATE deliberation with your fellow council members. "
            "Be candid. Analyse the proposal thoroughly from your perspective. "
            "Consider any peer arguments already shared."
        )
        user_msg = (
            f"PRESENTATION:\n{presentation}\n\n"
            f"Q&A TRANSCRIPT:\n{qa_transcript}\n\n"
            f"PEER DELIBERATION SO FAR:\n{peer_context}\n\n"
            "Share your position and reasoning."
        )
        position = self._call_llm(system, [{"role": "user", "content": user_msg}])
        return position

    def vote(
        self,
        presentation: str,
        qa_transcript: str,
        deliberation_transcript: str,
        session_type: str = "presentation",
    ) -> tuple[str, str, str]:
        """
        Cast a vote.  Returns (verdict, reasoning, conditions).
        For presentations: verdict is 'approved' | 'rejected' | 'modified'.
        For consultations: verdict is 'advice_given'.
        """
        if session_type == "consultation":
            system = self._build_system_prompt(
                "Provide your specific advice/recommendation. "
                "Start with 'ADVICE_GIVEN' on its own line, then your advice."
            )
            user_msg = (
                f"QUESTION:\n{presentation}\n\n"
                f"Q&A:\n{qa_transcript}\n\n"
                f"DELIBERATION:\n{deliberation_transcript}"
            )
            raw = self._call_llm(system, [{"role": "user", "content": user_msg}])
            return "advice_given", raw.replace("ADVICE_GIVEN", "").strip(), ""

        system = self._build_system_prompt(
            "Cast your vote on this proposal. "
            "Start your response with exactly one of: APPROVED, REJECTED, or MODIFIED.\n"
            "Then explain your reasoning.\n"
            "If MODIFIED, describe the required changes after '---CONDITIONS---'."
        )
        user_msg = (
            f"PROPOSAL:\n{presentation}\n\n"
            f"Q&A:\n{qa_transcript}\n\n"
            f"DELIBERATION:\n{deliberation_transcript}"
        )
        raw = self._call_llm(system, [{"role": "user", "content": user_msg}])
        return _parse_vote(raw)


# ---------------------------------------------------------------------------
# Vote parsing
# ---------------------------------------------------------------------------


def _parse_vote(raw: str) -> tuple[str, str, str]:
    """Parse LLM vote output into (verdict, reasoning, conditions)."""
    lines = raw.strip().splitlines()
    verdict = "rejected"
    for line in lines:
        upper = line.strip().upper()
        if upper.startswith("APPROVED"):
            verdict = "approved"
            break
        if upper.startswith("MODIFIED"):
            verdict = "modified"
            break
        if upper.startswith("REJECTED"):
            verdict = "rejected"
            break

    if "---CONDITIONS---" in raw:
        parts = raw.split("---CONDITIONS---", 1)
        reasoning = parts[0].strip()
        # Remove the verdict keyword from reasoning
        for kw in ("APPROVED", "REJECTED", "MODIFIED"):
            reasoning = reasoning.replace(kw, "").strip()
        conditions = parts[1].strip()
    else:
        reasoning = raw
        for kw in ("APPROVED", "REJECTED", "MODIFIED"):
            reasoning = reasoning.replace(kw, "").strip()
        conditions = ""

    return verdict, reasoning.strip(), conditions.strip()
