"""Persistent file storage for council sessions and personas."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from the_council.session import CouncilSession

_log = logging.getLogger(__name__)


class Storage:
    """
    Handles reading/writing council sessions and indexes to the `.council/` directory.

    Layout::

        .council/
          personas/       ← PersonaManager owns this
          sessions/
            <id>.md       ← human-readable session transcript
            <id>.json     ← machine-readable session data
          index.json      ← ordered list of session summaries
    """

    def __init__(self, council_dir: Path) -> None:
        self.council_dir = council_dir
        self.sessions_dir = council_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = council_dir / "index.json"

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def save_session(self, session: CouncilSession) -> None:
        """Persist *session* as both markdown and JSON."""
        md_path = self.sessions_dir / f"{session.id}.md"
        json_path = self.sessions_dir / f"{session.id}.json"

        md_path.write_text(session.to_markdown(), encoding="utf-8")
        json_path.write_text(json.dumps(self._session_to_json(session), indent=2), encoding="utf-8")
        self._update_index(session)

    def load_session_markdown(self, session_id: str) -> str | None:
        path = self.sessions_dir / f"{session_id}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return the session index, most recent first."""
        if not self._index_path.exists():
            return []
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            return list(reversed(data))
        except Exception:
            _log.warning("Failed to read session index at %s", self._index_path, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_to_json(self, session: CouncilSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "type": session.session_type.value,
            "created_at": session.created_at.isoformat(),
            "request": session.request,
            "context": session.context,
            "evidence": session.evidence,
            "verdict": session.verdict.value,
            "final_decision": session.final_decision,
            "participants": session.participants,
            "votes": [v.to_dict() for v in session.votes],
            "qa_messages": [m.to_dict() for m in session.qa_messages],
            "public_deliberation": [m.to_dict() for m in session.public_deliberation],
            "human_consulted": session.human_consulted,
        }

    def _update_index(self, session: CouncilSession) -> None:
        sessions = []
        if self._index_path.exists():
            try:
                sessions = json.loads(self._index_path.read_text(encoding="utf-8"))
            except Exception:
                _log.warning("Failed to read session index; starting fresh.", exc_info=True)
                sessions = []

        # Remove existing entry for the same id, then append updated
        sessions = [s for s in sessions if s.get("id") != session.id]
        sessions.append(
            {
                "id": session.id,
                "type": session.session_type.value,
                "created_at": session.created_at.isoformat(),
                "verdict": session.verdict.value,
                "request_summary": session.request[:120],
                "participants": session.participants,
            }
        )
        self._index_path.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
