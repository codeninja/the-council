"""Council orchestration – manages members, runs sessions, handles deadlocks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from the_council.member import CouncilMember
from the_council.message_queue import EventQueue
from the_council.personas import PersonaConfig, PersonaManager, _slugify
from the_council.session import CouncilSession, SessionType, Verdict, Vote
from the_council.storage import Storage


class Council:
    """
    The Council manages a collection of AI council members and orchestrates
    sessions (presentations and consultations).

    Session flow for **presentations**::

        1. Presentation Phase  – Claude Code states its business + evidence
        2. Q&A Phase           – each member may ask up to 3 clarifying questions
                                 (Claude Code answers them via the session context)
        3. Private Deliberation – members debate privately (not visible to Claude Code)
        4. Public Summary      – a brief public statement per member
        5. Voting Phase        – each member casts a vote (approved/rejected/modified)
        6. Verdict             – majority wins; deadlock → human consulted
        7. Session saved       – full transcript written to .council/sessions/

    Session flow for **consultations** is similar but skips voting in favour of
    synthesised advice.
    """

    def __init__(
        self,
        council_dir: Path | str | None = None,
        project_root: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.council_dir = Path(council_dir or ".council").resolve()
        self.project_root = str(Path(project_root or ".").resolve())
        # Kept for backward compatibility; per-persona provider env vars take precedence.
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

        self._persona_manager = PersonaManager(self.council_dir / "personas")
        self._storage = Storage(self.council_dir)
        self._queue = EventQueue()

        # Load existing personas → members
        # Each CouncilMember selects its own backend via persona.provider + persona.api_key.
        # The legacy global api_key is passed as a fallback for Anthropic personas that
        # carry no key of their own and have no ANTHROPIC_API_KEY env var set.
        self._members: dict[str, CouncilMember] = {}
        for persona in self._persona_manager.load_all():
            self._members[persona.slug] = CouncilMember(
                persona, self.project_root, self.api_key
            )

    # ------------------------------------------------------------------
    # Member management
    # ------------------------------------------------------------------

    def add_member(
        self,
        name: str,
        title: str,
        description: str,
        model: str = "claude-opus-4-5",
        provider: str = "anthropic",
        api_key: str = "",
        traits: list[str] | None = None,
        system_prompt: str = "",
    ) -> PersonaConfig:
        persona = PersonaConfig(
            name=name,
            title=title,
            description=description,
            model=model,
            provider=provider,
            api_key=api_key,
            traits=traits or [],
            system_prompt=system_prompt,
        )
        self._persona_manager.save(persona)
        self._members[persona.slug] = CouncilMember(persona, self.project_root)
        return persona

    def remove_member(self, name_or_slug: str) -> bool:
        slug = _slugify(name_or_slug)
        if slug in self._members:
            del self._members[slug]
        return self._persona_manager.delete(slug)

    def list_members(self) -> list[dict[str, Any]]:
        return [m.persona.to_dict() for m in self._members.values()]

    def get_member(self, name_or_slug: str) -> CouncilMember | None:
        slug = _slugify(name_or_slug)
        return self._members.get(slug)

    # ------------------------------------------------------------------
    # Presentation session
    # ------------------------------------------------------------------

    def present(
        self,
        request: str,
        evidence: str = "",
        context: str = "",
        human_input_callback: Any = None,
    ) -> CouncilSession:
        """
        Run a full presentation session.

        *human_input_callback* is an optional callable ``(question: str) -> str``
        that is invoked if the council deadlocks and needs human input.
        """
        session = CouncilSession(
            session_type=SessionType.PRESENTATION,
            request=request,
            evidence=evidence,
            context=context,
            participants=[m.name for m in self._members.values()],
        )

        if not self._members:
            session.verdict = Verdict.DEADLOCKED
            session.final_decision = (
                "The council has no members. "
                "Add members with `council_create_member` before presenting."
            )
            self._storage.save_session(session)
            return session

        presentation_text = _build_presentation(request, evidence, context)
        qa_topic = f"session:{session.id}:qa"
        deliberation_topic = f"session:{session.id}:deliberation"

        # ---------------------------------------------------------------
        # Phase 1: Q&A
        # ---------------------------------------------------------------
        for member in self._members.values():
            questions = member.ask_questions(presentation_text)
            for q in questions:
                msg = self._queue_sync_publish(qa_topic, member.name, q)
                session.qa_messages.append(msg)

        # ---------------------------------------------------------------
        # Phase 2: Private deliberation
        # ---------------------------------------------------------------
        qa_transcript = _messages_to_transcript(session.qa_messages)
        positions: list[str] = []

        for member in self._members.values():
            position = member.deliberate(
                presentation_text,
                qa_transcript,
                positions,
                self._queue,
                deliberation_topic,
            )
            msg = self._queue_sync_publish(deliberation_topic, member.name, position)
            session.private_deliberation.append(msg)
            positions.append(f"[{member.name}] {position}")

        # ---------------------------------------------------------------
        # Phase 3: Public summary (brief statement per member)
        # ---------------------------------------------------------------
        pub_topic = f"session:{session.id}:public"
        deliberation_transcript = _messages_to_transcript(session.private_deliberation)

        for member in self._members.values():
            summary = _summarise_position(member, presentation_text, deliberation_transcript)
            msg = self._queue_sync_publish(pub_topic, member.name, summary)
            session.public_deliberation.append(msg)

        # ---------------------------------------------------------------
        # Phase 4: Voting
        # ---------------------------------------------------------------
        for member in self._members.values():
            raw_verdict, reasoning, conditions = member.vote(
                presentation_text,
                qa_transcript,
                deliberation_transcript,
                session_type="presentation",
            )
            try:
                verdict_enum = Verdict(raw_verdict)
            except ValueError:
                verdict_enum = Verdict.REJECTED
            session.votes.append(
                Vote(
                    member_name=member.name,
                    verdict=verdict_enum,
                    reasoning=reasoning,
                    conditions=conditions,
                )
            )

        # ---------------------------------------------------------------
        # Phase 5: Tally
        # ---------------------------------------------------------------
        session.verdict, session.final_decision, session.human_consulted = _tally_votes(
            session.votes, human_input_callback, session
        )

        self._storage.save_session(session)
        return session

    # ------------------------------------------------------------------
    # Consultation session
    # ------------------------------------------------------------------

    def consult(
        self,
        question: str,
        context: str = "",
    ) -> CouncilSession:
        """Ask the council for advice (no formal vote)."""
        session = CouncilSession(
            session_type=SessionType.CONSULTATION,
            request=question,
            context=context,
            participants=[m.name for m in self._members.values()],
        )

        if not self._members:
            session.verdict = Verdict.ADVICE_GIVEN
            session.final_decision = (
                "The council has no members. "
                "Add members with `council_create_member` before consulting."
            )
            self._storage.save_session(session)
            return session

        presentation_text = f"QUESTION:\n{question}\n\nCONTEXT:\n{context}" if context else f"QUESTION:\n{question}"
        qa_topic = f"session:{session.id}:qa"
        deliberation_topic = f"session:{session.id}:deliberation"

        # Q&A
        for member in self._members.values():
            questions = member.ask_questions(presentation_text)
            for q in questions:
                msg = self._queue_sync_publish(qa_topic, member.name, q)
                session.qa_messages.append(msg)

        # Deliberation
        qa_transcript = _messages_to_transcript(session.qa_messages)
        positions: list[str] = []

        for member in self._members.values():
            position = member.deliberate(
                presentation_text,
                qa_transcript,
                positions,
                self._queue,
                deliberation_topic,
            )
            msg = self._queue_sync_publish(deliberation_topic, member.name, position)
            session.private_deliberation.append(msg)
            positions.append(f"[{member.name}] {position}")

        # Advice (no vote)
        deliberation_transcript = _messages_to_transcript(session.private_deliberation)
        advice_parts: list[str] = []

        for member in self._members.values():
            _, advice, _ = member.vote(
                presentation_text,
                qa_transcript,
                deliberation_transcript,
                session_type="consultation",
            )
            msg = self._queue_sync_publish(
                f"session:{session.id}:public", member.name, advice
            )
            session.public_deliberation.append(msg)
            advice_parts.append(f"**{member.name}:** {advice}")

        session.verdict = Verdict.ADVICE_GIVEN
        session.final_decision = "\n\n".join(advice_parts)
        self._storage.save_session(session)
        return session

    # ------------------------------------------------------------------
    # Session history
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        return self._storage.list_sessions()

    def get_session(self, session_id: str) -> str | None:
        return self._storage.load_session_markdown(session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _queue_sync_publish(self, topic: str, sender: str, content: str) -> Any:
        """Synchronous wrapper around the async queue for use in sync Council methods."""
        import asyncio

        try:
            asyncio.get_running_loop()
            # We're inside a running event loop – dispatch to a thread to avoid deadlock
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(asyncio.run, self._queue.publish(topic, sender, content))
                return future.result()
        except RuntimeError:
            # No running loop – safe to call asyncio.run directly
            return asyncio.run(self._queue.publish(topic, sender, content))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_presentation(request: str, evidence: str, context: str) -> str:
    parts = [f"REQUEST:\n{request}"]
    if evidence:
        parts.append(f"EVIDENCE:\n{evidence}")
    if context:
        parts.append(f"CONTEXT:\n{context}")
    return "\n\n".join(parts)


def _messages_to_transcript(messages: list[Any]) -> str:
    if not messages:
        return "(none)"
    return "\n".join(f"[{m.sender}] {m.content}" for m in messages)


def _summarise_position(member: CouncilMember, presentation: str, deliberation: str) -> str:
    """Ask a member for a short public summary of their position."""
    system = member._build_system_prompt(
        "In 2-3 sentences, state your public position on this proposal for the record. "
        "Be clear and direct."
    )
    user_msg = f"PROPOSAL:\n{presentation}\n\nYOUR DELIBERATION:\n{deliberation}"
    return member._call_llm(system, [{"role": "user", "content": user_msg}])


def _tally_votes(
    votes: list[Vote],
    human_callback: Any,
    session: CouncilSession,
) -> tuple[Verdict, str, bool]:
    """Tally votes and return (final_verdict, decision_text, human_consulted)."""
    counts: dict[str, int] = {}
    for v in votes:
        counts[v.verdict.value] = counts.get(v.verdict.value, 0) + 1

    if not counts:
        return Verdict.DEADLOCKED, "No votes were cast.", False

    top_count = max(counts.values())
    leaders = [k for k, c in counts.items() if c == top_count]

    # Majority
    if len(leaders) == 1:
        verdict = Verdict(leaders[0])
        reasoning = _build_decision_text(votes, verdict)
        return verdict, reasoning, False

    # Deadlock – ask the human if a callback is provided
    if human_callback is not None:
        tie_summary = ", ".join(f"{k}={v}" for k, v in counts.items())
        human_question = (
            f"The council is deadlocked ({tie_summary}). "
            "Please cast the deciding vote: approve, reject, or modify?"
        )
        human_answer = human_callback(human_question)
        answer_lower = (human_answer or "").lower().strip()
        if "approve" in answer_lower:
            verdict = Verdict.APPROVED
        elif "modify" in answer_lower:
            verdict = Verdict.MODIFIED
        else:
            verdict = Verdict.REJECTED
        decision = f"Human broke the deadlock: {human_answer}\n\n{_build_decision_text(votes, verdict)}"
        return verdict, decision, True

    return Verdict.DEADLOCKED, _build_decision_text(votes, Verdict.DEADLOCKED), False


def _build_decision_text(votes: list[Vote], final_verdict: Verdict) -> str:
    lines = [f"**Final Verdict: {final_verdict.value.upper()}**\n"]
    for v in votes:
        lines.append(f"- **{v.member_name}** voted `{v.verdict.value}`: {v.reasoning}")
        if v.conditions:
            lines.append(f"  *Required changes:* {v.conditions}")
    return "\n".join(lines)
