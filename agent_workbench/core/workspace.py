from __future__ import annotations

import difflib
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


class WorkspaceViolation(PermissionError):
    """Raised when a requested path escapes the workspace boundary."""


@dataclass(slots=True)
class EditProposal:
    approval_id: str
    relative_path: str
    expected_text: str
    replacement_text: str
    original_text: str
    updated_text: str
    diff: str
    status: str = "pending"


class ApprovalRequired(RuntimeError):
    def __init__(self, proposal: EditProposal) -> None:
        super().__init__(f"approval required: {proposal.approval_id}")
        self.proposal = proposal


class Workspace:
    IGNORED_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__"}
    EDITABLE_SUFFIXES = {
        ".c", ".cc", ".cpp", ".css", ".csv", ".go", ".h", ".hpp", ".html", ".ini", ".java",
        ".js", ".json", ".jsx", ".md", ".py", ".rs", ".sh", ".sql", ".toml", ".ts", ".tsx",
        ".txt", ".xml", ".yaml", ".yml",
    }

    def __init__(self, root_dir: str | Path, *, max_read_bytes: int = 512_000, max_depth: int = 12) -> None:
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_read_bytes = max_read_bytes
        self.max_depth = max_depth
        self._proposals: dict[str, EditProposal] = {}

    def resolve_path(self, relative_path: str) -> Path:
        raw = str(relative_path).strip()
        windows_path = PureWindowsPath(raw)
        if not raw or windows_path.is_absolute() or windows_path.drive or raw.startswith(("\\\\", "//")):
            raise WorkspaceViolation(f"path must be relative to workspace: {relative_path}")
        target = (self.root / raw).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceViolation(f"path escapes workspace: {relative_path}") from exc
        return target

    def _read_text(self, relative_path: str) -> str:
        target = self.resolve_path(relative_path)
        if not target.is_file():
            raise FileNotFoundError(relative_path)
        if target.stat().st_size > self.max_read_bytes:
            raise ValueError(f"file exceeds {self.max_read_bytes} byte read limit")
        try:
            text = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("file is not UTF-8 text") from exc
        if "\x00" in text:
            raise ValueError("binary text is not supported")
        return text

    def read_file(self, relative_path: str, start_line: int = 1, max_lines: int = 400) -> str:
        text = self._read_text(relative_path)
        if start_line < 1 or max_lines < 1 or max_lines > 5_000:
            raise ValueError("start_line and max_lines are outside the allowed range")
        lines = text.splitlines()
        selected = "\n".join(lines[start_line - 1 : start_line - 1 + max_lines])
        if start_line == 1 and len(lines) <= max_lines and text.endswith(("\n", "\r")):
            selected += "\n"
        return selected

    def list_files(self, directory: str = ".", pattern: str = "*") -> list[str]:
        root = self.resolve_path(directory)
        if not root.is_dir():
            raise NotADirectoryError(directory)
        files = []
        directories = [directory]
        while directories:
            for entry in self.list_directory(directories.pop()):
                if entry["type"] == "directory":
                    directories.append(entry["path"])
                elif Path(entry["path"]).relative_to(root.relative_to(self.root)).match(pattern):
                    files.append(entry["path"])
        return sorted(files)

    def list_directory(self, directory: str = ".") -> list[dict[str, str]]:
        root = self.resolve_path(directory)
        if not root.is_dir():
            raise NotADirectoryError(directory)
        relative = root.relative_to(self.root)
        if any(part.casefold() in self.IGNORED_DIRECTORIES for part in relative.parts):
            return []
        if len(relative.parts) >= self.max_depth:
            return []
        entries = []
        with os.scandir(root) as children:
            for child in children:
                try:
                    info = child.stat(follow_symlinks=False)
                    if child.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        continue
                    is_dir = child.is_dir(follow_symlinks=False)
                    if is_dir and (child.name.casefold() in self.IGNORED_DIRECTORIES or len(relative.parts) + 1 >= self.max_depth):
                        continue
                    if not is_dir and not child.is_file(follow_symlinks=False):
                        continue
                    path = Path(child.path).relative_to(self.root).as_posix()
                    self.resolve_path(path)
                    entries.append({"name": child.name, "path": path,
                                    "type": "directory" if is_dir else "file"})
                except OSError:
                    continue
        return sorted(entries, key=lambda entry: (entry["type"] != "directory", entry["name"].casefold()))

    def grep_search(self, query: str, directory: str = ".", *, max_results: int = 50) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for relative in self.list_files(directory):
            try:
                text = self._read_text(relative)
            except (ValueError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if query.casefold() in line.casefold():
                    results.append({"path": relative, "line": line_number, "text": line})
                    if len(results) >= max_results:
                        return results
        return results

    def propose_edit(self, path: str, expected_text: str, replacement_text: str) -> None:
        if Path(path).suffix.casefold() not in self.EDITABLE_SUFFIXES:
            raise ValueError("file is not an editable text type")
        original = self._read_text(path)
        if expected_text not in original:
            raise ValueError("expected text was not found; file may have changed")
        updated = original.replace(expected_text, replacement_text, 1)
        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        proposal = EditProposal(
            approval_id=f"approval-{uuid.uuid4().hex}",
            relative_path=path,
            expected_text=expected_text,
            replacement_text=replacement_text,
            original_text=original,
            updated_text=updated,
            diff=diff,
        )
        self._proposals[proposal.approval_id] = proposal
        raise ApprovalRequired(proposal)

    def approve_edit(self, approval_id: str) -> EditProposal:
        proposal = self._proposals[approval_id]
        if proposal.status == "applied":
            return proposal
        if proposal.status != "pending":
            raise ValueError("approval is no longer pending")
        target = self.resolve_path(proposal.relative_path)
        current = self._read_text(proposal.relative_path)
        if current != proposal.original_text:
            proposal.status = "stale"
            raise ValueError("file changed after proposal was created")
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(proposal.updated_text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        proposal.status = "applied"
        return proposal

    def reject_edit(self, approval_id: str) -> EditProposal:
        proposal = self._proposals[approval_id]
        if proposal.status == "pending":
            proposal.status = "rejected"
        return proposal
