"""Tests for persona loading/saving."""

from __future__ import annotations

from pathlib import Path

import pytest

from the_council.personas import PersonaConfig, PersonaManager, _slugify


class TestSlugify:
    def test_simple(self) -> None:
        assert _slugify("Linus Trivolds") == "linus_trivolds"

    def test_special_chars(self) -> None:
        assert _slugify("The Architect!") == "the_architect"

    def test_already_slug(self) -> None:
        assert _slugify("ada_lovelace") == "ada_lovelace"


class TestPersonaConfig:
    def test_slug_auto_generated(self) -> None:
        p = PersonaConfig(name="Ada Lovelace", title="T", description="D")
        assert p.slug == "ada_lovelace"

    def test_explicit_slug_preserved(self) -> None:
        p = PersonaConfig(name="Ada Lovelace", title="T", description="D", slug="custom_slug")
        assert p.slug == "custom_slug"

    def test_to_markdown_roundtrip(self) -> None:
        p = PersonaConfig(
            name="Test Member",
            title="Tester",
            description="Tests things.",
            model="claude-haiku-4-5",
            traits=["curious", "thorough"],
            system_prompt="You test.",
        )
        md = p.to_markdown()
        restored = PersonaConfig.from_markdown(md)
        assert restored.name == p.name
        assert restored.title == p.title
        assert restored.model == p.model
        assert restored.description == p.description
        assert restored.traits == p.traits
        assert restored.system_prompt == p.system_prompt

    def test_to_dict(self) -> None:
        p = PersonaConfig(name="X", title="Y", description="Z", traits=["a"])
        d = p.to_dict()
        assert d["name"] == "X"
        assert d["traits"] == ["a"]
        assert "slug" in d

    def test_from_markdown_minimal(self) -> None:
        md = "# Simple Member\n\n**Title:** Just a member  \n\n## Description\n\nDoes stuff.\n"
        p = PersonaConfig.from_markdown(md)
        assert p.name == "Simple Member"
        assert p.title == "Just a member"
        assert p.description == "Does stuff."


class TestPersonaManager:
    @pytest.fixture
    def manager(self, tmp_path: Path) -> PersonaManager:
        return PersonaManager(tmp_path / "personas")

    def test_save_and_load(self, manager: PersonaManager) -> None:
        p = PersonaConfig(
            name="Test Member",
            title="Tester",
            description="Tests things.",
            traits=["detail-oriented"],
        )
        manager.save(p)
        loaded = manager.load("test_member")
        assert loaded is not None
        assert loaded.name == "Test Member"
        assert loaded.traits == ["detail-oriented"]

    def test_load_nonexistent(self, manager: PersonaManager) -> None:
        assert manager.load("nonexistent") is None

    def test_load_all(self, manager: PersonaManager) -> None:
        p1 = PersonaConfig(name="Alice", title="T", description="D")
        p2 = PersonaConfig(name="Bob", title="T", description="D")
        manager.save(p1)
        manager.save(p2)
        all_personas = manager.load_all()
        names = {p.name for p in all_personas}
        assert names == {"Alice", "Bob"}

    def test_delete(self, manager: PersonaManager) -> None:
        p = PersonaConfig(name="Temp", title="T", description="D")
        manager.save(p)
        assert manager.exists("temp")
        assert manager.delete("temp")
        assert not manager.exists("temp")

    def test_delete_nonexistent(self, manager: PersonaManager) -> None:
        assert not manager.delete("ghost")

    def test_exists(self, manager: PersonaManager) -> None:
        p = PersonaConfig(name="Exists", title="T", description="D")
        assert not manager.exists("exists")
        manager.save(p)
        assert manager.exists("exists")
