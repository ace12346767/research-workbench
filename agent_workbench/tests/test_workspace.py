from __future__ import annotations

from pathlib import Path

import pytest

from agent_workbench.core.workspace import ApprovalRequired, Workspace, WorkspaceViolation


def test_workspace_rejects_parent_escape(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "project")
    with pytest.raises(WorkspaceViolation):
        workspace.read_file("../secret.txt")


def test_edit_requires_approval_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "main.py"
    target.write_text("answer = 1\n", encoding="utf-8")
    workspace = Workspace(root)

    with pytest.raises(ApprovalRequired) as raised:
        workspace.propose_edit("main.py", "answer = 1", "answer = 2")

    proposal = raised.value.proposal
    assert "-answer = 1" in proposal.diff
    assert "+answer = 2" in proposal.diff
    assert target.read_text(encoding="utf-8") == "answer = 1\n"

    first = workspace.approve_edit(proposal.approval_id)
    second = workspace.approve_edit(proposal.approval_id)

    assert first.status == "applied"
    assert second.status == "applied"
    assert target.read_text(encoding="utf-8") == "answer = 2\n"


def test_edit_rejects_stale_expected_text(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("answer = 3\n", encoding="utf-8")
    workspace = Workspace(root)

    with pytest.raises(ValueError, match="expected text"):
        workspace.propose_edit("main.py", "answer = 1", "answer = 2")


def test_workspace_supports_ranged_reads_and_ignores_dependency_directories(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "node_modules").mkdir()
    (root / "src" / "main.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    (root / ".git" / "config").write_text("secret", encoding="utf-8")
    (root / "node_modules" / "pkg.js").write_text("dependency", encoding="utf-8")
    workspace = Workspace(root)

    assert workspace.read_file("src/main.py", start_line=2, max_lines=2) == "two\nthree"
    assert workspace.list_files() == ["src/main.py"]


def test_workspace_rejects_editing_unsupported_binary_like_extension(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "image.bin").write_bytes(b"plain bytes")
    workspace = Workspace(root)

    with pytest.raises(ValueError, match="editable text type"):
        workspace.propose_edit("image.bin", "plain", "changed")


def test_search_reads_past_preview_limit(tmp_path):
    (tmp_path / "long.txt").write_text("keep\n" * 600 + "needle\n", encoding="utf-8")
    assert Workspace(tmp_path).grep_search("needle") == [
        {"path": "long.txt", "line": 601, "text": "needle"}]


def test_ignored_directories_are_pruned_before_traversal(tmp_path, monkeypatch):
    import os
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / "hidden.py").write_text("hidden")
    (tmp_path / "main.py").write_text("main")
    original = os.scandir
    def guarded(path):
        assert Path(path) != ignored, "ignored directory must never be scanned"
        return original(path)
    monkeypatch.setattr(os, "scandir", guarded)
    assert Workspace(tmp_path).list_files(pattern="*.py") == ["main.py"]


def test_directory_listing_is_lazy_and_depth_bounded(tmp_path):
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("main")
    (tmp_path / "root.txt").write_text("root")
    workspace = Workspace(tmp_path, max_depth=2)
    assert workspace.list_directory() == [
        {"name": "src", "path": "src", "type": "directory"},
        {"name": "root.txt", "path": "root.txt", "type": "file"},
    ]
    assert workspace.list_directory("src") == [
        {"name": "main.py", "path": "src/main.py", "type": "file"}]
