from __future__ import annotations

from pathlib import Path

import pytest

from agent_platform.application.research_graph_manager import ResearchGraphManager
from agent_platform.domain.exceptions import RequestValidationError
from agent_platform.infrastructure.kuzu_client import KuzuGateway


def _db(tmp_path: Path) -> KuzuGateway:
    return KuzuGateway(str(tmp_path / "research_meta.kuzu"))


def test_research_graph_root_is_idempotent(tmp_path: Path) -> None:
    manager = ResearchGraphManager()
    db = _db(tmp_path)

    first = manager.ensure_root(db, "Investigate AI warehouse robotics", source_trace_id="trace-1")
    second = manager.ensure_root(db, "Different prompt should not replace root", source_trace_id="trace-2")

    assert first.id == second.id
    assert second.body == "Investigate AI warehouse robotics"
    assert manager.root(db) is not None


def test_research_graph_supports_branch_growth_and_convergence(tmp_path: Path) -> None:
    manager = ResearchGraphManager()
    db = _db(tmp_path)
    root = manager.ensure_root(db, "Investigate AI warehouse robotics")

    first = manager.advance_new(
        db,
        root.id,
        {
            "kind": "progress",
            "content": "Warehouse automation economics point toward labor bottlenecks.",
            "transition_kind": "finding",
            "evidence_kind": "synthesis",
        },
        {
            "kind": "question",
            "title": "What labor bottlenecks matter most?",
            "body": "Map the bottlenecks that drive automation ROI in warehouses.",
        },
        prompt="Investigate AI warehouse robotics",
    )
    second = manager.advance_new(
        db,
        root.id,
        {
            "kind": "progress",
            "content": "Partner ecosystems also point toward labor bottlenecks.",
            "transition_kind": "idea",
            "evidence_kind": "synthesis",
        },
        {
            "kind": "task",
            "title": "Review partner ecosystem",
            "body": "Review retailers and logistics partners tied to the automation stack.",
        },
        prompt="Investigate AI warehouse robotics",
    )

    converged = manager.advance_existing(
        db,
        second["target_node"]["id"],
        first["target_node"]["id"],
        {
            "kind": "progress",
            "content": "Partner evidence converges on the same labor bottleneck question.",
            "transition_kind": "finding",
            "evidence_kind": "graph",
        },
        prompt="Investigate AI warehouse robotics",
    )

    frontier = manager.get_frontier(db, prompt="Investigate AI warehouse robotics")
    ancestry = manager.get_ancestry(db, first["target_node"]["id"], depth=2, prompt="Investigate AI warehouse robotics")

    assert converged["target_node"]["id"] == first["target_node"]["id"]
    assert [item.id for item in frontier] == [first["target_node"]["id"]]
    assert len(ancestry) == 3


def test_research_graph_dead_end_requires_failure_node(tmp_path: Path) -> None:
    manager = ResearchGraphManager()
    db = _db(tmp_path)
    root = manager.ensure_root(db, "Investigate poultry monitoring")

    failure = manager.advance_new(
        db,
        root.id,
        {
            "kind": "dead_end",
            "content": "This vendor claim could not be verified from primary sources.",
            "transition_kind": "failure",
            "evidence_kind": "web",
        },
        {
            "kind": "failure",
            "title": "Unverified vendor claim",
            "body": "The claim appears promotional and lacks corroboration.",
            "failure_kind": "insufficient_evidence",
        },
        prompt="Investigate poultry monitoring",
    )

    descendants = manager.get_descendants(
        db,
        root.id,
        depth=2,
        mode="terminal",
        prompt="Investigate poultry monitoring",
    )

    assert failure["target_node"]["kind"] == "failure"
    assert descendants[0]["to_node"]["failure_kind"] == "insufficient_evidence"


def test_research_graph_rejects_invalid_progress_to_failure(tmp_path: Path) -> None:
    manager = ResearchGraphManager()
    db = _db(tmp_path)
    root = manager.ensure_root(db, "Investigate AI datacenter cooling")

    with pytest.raises(RequestValidationError):
        manager.advance_new(
            db,
            root.id,
            {"kind": "progress", "content": "This should fail."},
            {
                "kind": "failure",
                "title": "Invalid failure node",
                "body": "Failure nodes are only valid for dead_end transitions.",
            },
            prompt="Investigate AI datacenter cooling",
        )


def test_research_graph_search_finds_existing_candidate(tmp_path: Path) -> None:
    manager = ResearchGraphManager()
    db = _db(tmp_path)
    root = manager.ensure_root(db, "Investigate AI networking")
    manager.advance_new(
        db,
        root.id,
        {
            "kind": "progress",
            "content": "GPU cluster research points to interconnect bottlenecks.",
            "transition_kind": "finding",
            "evidence_kind": "web",
        },
        {
            "kind": "question",
            "title": "Map Nvidia cluster bottlenecks",
            "body": "Identify the networking and optical chokepoints inside large GPU clusters.",
        },
        prompt="Investigate AI networking",
    )

    results = manager.search_nodes(db, "nvidia gpu cluster bottlenecks", limit=5, prompt="Investigate AI networking")

    assert results
    assert results[0].title == "Map Nvidia cluster bottlenecks"


def test_research_graph_lazy_initializes_on_first_check(tmp_path: Path) -> None:
    manager = ResearchGraphManager()
    db = _db(tmp_path)

    frontier = manager.get_frontier(db, prompt="Investigate autonomous warehouses")

    assert frontier
    assert frontier[0].is_root is True
    assert manager.root(db) is not None
