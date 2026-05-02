"""Persona configuration and management for council members."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class PersonaConfig:
    """Configuration for a council member persona."""

    name: str
    title: str
    description: str
    model: str = "claude-opus-4-5"
    #: AI provider: ``"anthropic"`` | ``"openai"`` | ``"openrouter"`` | ``"ollama"``
    provider: str = "anthropic"
    #: Optional per-persona API key override.  When empty the provider's env var is used.
    #: Stored in the persona file – only set this when you intentionally want the key
    #: embedded in the file rather than read from the environment.
    api_key: str = ""
    traits: list[str] = field(default_factory=list)
    system_prompt: str = ""
    slug: str = ""

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = _slugify(self.name)

    def to_markdown(self) -> str:
        """Serialise to a `.council/personas/<slug>.md` file."""
        lines = [
            f"# {self.name}\n",
            f"**Title:** {self.title}  ",
            f"**Model:** {self.model}  ",
            f"**Provider:** {self.provider}  \n",
        ]
        if self.api_key:
            lines.append(f"**API Key:** {self.api_key}  \n")
        lines.append(f"## Description\n\n{self.description}\n")
        if self.traits:
            lines.append("## Traits\n")
            for t in self.traits:
                lines.append(f"- {t}")
            lines.append("")
        if self.system_prompt:
            lines.append(f"## System Prompt\n\n{self.system_prompt}\n")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "title": self.title,
            "model": self.model,
            "provider": self.provider,
            "description": self.description,
            "traits": self.traits,
            "system_prompt": self.system_prompt,
            # api_key intentionally omitted to avoid leaking secrets in list output
        }

    @classmethod
    def from_markdown(cls, text: str) -> PersonaConfig:
        """Parse a persona markdown file."""
        name = _extract_heading(text, 1) or "Unknown"
        title = _extract_inline_field(text, "Title") or ""
        model = _extract_inline_field(text, "Model") or "claude-opus-4-5"
        provider = _extract_inline_field(text, "Provider") or "anthropic"
        api_key = _extract_inline_field(text, "API Key") or ""
        description = _extract_section(text, "Description") or ""
        traits = _extract_list(text, "Traits")
        system_prompt = _extract_section(text, "System Prompt") or ""
        return cls(
            name=name,
            title=title,
            model=model,
            provider=provider,
            api_key=api_key,
            description=description,
            traits=traits,
            system_prompt=system_prompt,
        )


# ---------------------------------------------------------------------------
# Markdown parsing helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug.strip("_")


def _extract_heading(text: str, level: int) -> str | None:
    prefix = "#" * level + " "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _extract_inline_field(text: str, field_name: str) -> str | None:
    pattern = rf"\*\*{re.escape(field_name)}:\*\*\s*(.+)"
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


def _extract_section(text: str, section: str) -> str | None:
    """Extract content between ## section and the next ## heading."""
    pattern = rf"## {re.escape(section)}\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else None


def _extract_list(text: str, section: str) -> list[str]:
    """Extract a markdown bullet list from a section."""
    body = _extract_section(text, section)
    if not body:
        return []
    items = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
    return items


# ---------------------------------------------------------------------------
# Persona file manager
# ---------------------------------------------------------------------------


class PersonaManager:
    """Load, save, and manage persona files in `.council/personas/`."""

    def __init__(self, personas_dir: Path) -> None:
        self.personas_dir = personas_dir
        self.personas_dir.mkdir(parents=True, exist_ok=True)

    def save(self, persona: PersonaConfig) -> Path:
        path = self.personas_dir / f"{persona.slug}.md"
        path.write_text(persona.to_markdown(), encoding="utf-8")
        return path

    def load(self, slug: str) -> PersonaConfig | None:
        path = self.personas_dir / f"{slug}.md"
        if not path.exists():
            return None
        return PersonaConfig.from_markdown(path.read_text(encoding="utf-8"))

    def load_all(self) -> list[PersonaConfig]:
        personas = []
        for path in sorted(self.personas_dir.glob("*.md")):
            try:
                personas.append(PersonaConfig.from_markdown(path.read_text(encoding="utf-8")))
            except Exception:
                _log.warning("Failed to load persona from %s", path, exc_info=True)
        return personas

    def delete(self, slug: str) -> bool:
        path = self.personas_dir / f"{slug}.md"
        if path.exists():
            path.unlink()
            return True
        return False

    def exists(self, slug: str) -> bool:
        return (self.personas_dir / f"{slug}.md").exists()
