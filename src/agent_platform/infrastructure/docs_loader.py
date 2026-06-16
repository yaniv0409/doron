from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_platform.config.settings import DocumentationSettings
from agent_platform.domain.exceptions import DocumentationError


@dataclass(slots=True)
class DocumentationSection:
    source_id: str
    title: str
    body: str


class DocumentationRepository:
    def __init__(self, settings: DocumentationSettings) -> None:
        self._reference_path = settings.kuzu_reference_path

    def lookup(self, query: str) -> DocumentationSection:
        if not self._reference_path.exists():
            raise DocumentationError(
                f"documentation file not found: {self._reference_path}",
            )
        content = self._reference_path.read_text(encoding="utf-8")
        sections = _parse_sections(content)
        match = _select_section(query, sections)
        if not match:
            raise DocumentationError(f"no documentation match for: {query}")
        return match


def _parse_sections(content: str) -> list[DocumentationSection]:
    sections: list[DocumentationSection] = []
    current_title = "overview"
    current_body: list[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current_body:
                sections.append(
                    DocumentationSection(
                        source_id=current_title.lower().replace(" ", "-"),
                        title=current_title,
                        body="\n".join(current_body).strip(),
                    )
                )
            current_title = line[3:].strip()
            current_body = []
            continue
        current_body.append(line)
    if current_body:
        sections.append(
            DocumentationSection(
                source_id=current_title.lower().replace(" ", "-"),
                title=current_title,
                body="\n".join(current_body).strip(),
            )
        )
    return sections


def _select_section(
    query: str,
    sections: list[DocumentationSection],
) -> DocumentationSection | None:
    lowered = query.lower()
    best_match: DocumentationSection | None = None
    best_score = -1
    tokens = [token for token in lowered.split() if token]
    for section in sections:
        haystack = f"{section.title}\n{section.body}".lower()
        score = 0
        if lowered in haystack:
            score += 10
        for token in tokens:
            score += haystack.count(token)
            if token in section.title.lower():
                score += 5
        if score > best_score:
            best_score = score
            best_match = section
    return best_match
