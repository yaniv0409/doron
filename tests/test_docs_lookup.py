from pathlib import Path

from agent_platform.config.settings import DocumentationSettings
from agent_platform.infrastructure.docs_loader import DocumentationRepository


def test_docs_lookup_matches_local_reference() -> None:
    repository = DocumentationRepository(
        DocumentationSettings(
            kuzu_reference_path=Path("docs/kuzu-notes.md"),
        )
    )
    section = repository.lookup("schema")
    assert section.source_id == "schema-introspection"
    assert "schema-inspection tool" in section.body
