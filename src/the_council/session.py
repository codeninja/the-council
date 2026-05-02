"""Session data models for council interactions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from the_council.message_queue import Message


class SessionType(StrEnum):
    PRESENTATION = "presentation"
    CONSULTATION = "consultation"


class Verdict(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    DEADLOCKED = "deadlocked"
    ADVICE_GIVEN = "advice_given"


@dataclass
class Vote:
    member_name: str
    verdict: Verdict
    reasoning: str
    conditions: str = ""  # conditions if verdict is MODIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "member": self.member_name,
            "verdict": self.verdict.value,
            "reasoning": self.reasoning,
            "conditions": self.conditions,
        }


@dataclass
class CouncilSession:
    """
    A complete record of a council interaction, including the public Q&A,
    private deliberation, individual votes, and the final decision.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    session_type: SessionType = SessionType.PRESENTATION
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Input
    request: str = ""
    context: str = ""
    evidence: str = ""

    # Public phases
    qa_messages: list[Message] = field(default_factory=list)
    public_deliberation: list[Message] = field(default_factory=list)

    # Private deliberation (not exposed to Claude Code)
    private_deliberation: list[Message] = field(default_factory=list)

    # Outcome
    votes: list[Vote] = field(default_factory=list)
    verdict: Verdict = Verdict.ADVICE_GIVEN
    final_decision: str = ""
    participants: list[str] = field(default_factory=list)
    human_consulted: bool = False

    def summary_verdict(self) -> str:
        """One-line summary of the verdict."""
        counts: dict[str, int] = {}
        for v in self.votes:
            counts[v.verdict.value] = counts.get(v.verdict.value, 0) + 1
        if not counts:
            return self.verdict.value
        top = max(counts, key=lambda k: counts[k])
        return f"{top} ({counts[top]}/{len(self.votes)})"

    def to_markdown(self) -> str:
        """Render the full session as a human-readable markdown document."""
        lines: list[str] = []

        lines.append(f"# Council Session `{self.id}`\n")
        lines.append(f"**Type:** {self.session_type.value}  ")
        lines.append(f"**Date:** {self.created_at.strftime('%Y-%m-%d %H:%M UTC')}  ")
        lines.append(f"**Participants:** {', '.join(self.participants) or 'none'}  \n")

        lines.append("## Request\n")
        lines.append(self.request + "\n")

        if self.evidence:
            lines.append("## Evidence\n")
            lines.append(self.evidence + "\n")

        if self.context:
            lines.append("## Context\n")
            lines.append(self.context + "\n")

        if self.qa_messages:
            lines.append("## Q&A\n")
            for m in self.qa_messages:
                lines.append(f"**{m.sender}:** {m.content}\n")

        if self.public_deliberation:
            lines.append("## Public Deliberation\n")
            for m in self.public_deliberation:
                lines.append(f"**{m.sender}:** {m.content}\n")

        lines.append("## Votes\n")
        if self.votes:
            for v in self.votes:
                cond = f"  *Conditions:* {v.conditions}" if v.conditions else ""
                lines.append(f"- **{v.member_name}** → `{v.verdict.value}`  ")
                lines.append(f"  {v.reasoning}{cond}\n")
        else:
            lines.append("*No votes recorded.*\n")

        lines.append(f"## Final Decision: `{self.verdict.value.upper()}`\n")
        lines.append(self.final_decision + "\n")

        if self.human_consulted:
            lines.append("> ⚠️ Human was consulted to break a deadlock.\n")

        return "\n".join(lines)
