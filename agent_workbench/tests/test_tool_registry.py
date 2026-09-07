from __future__ import annotations

from agent_workbench.tools.registry import ToolRegistry


def test_tool_schema_uses_annotations_and_required_parameters() -> None:
    registry = ToolRegistry()

    def search(path: str, top_k: int = 5, tags: list[str] | None = None) -> list[str]:
        return []

    registry.register("search", "Search files", search)
    parameters = registry.schemas()[0]["function"]["parameters"]

    assert parameters["required"] == ["path"]
    assert parameters["properties"]["path"] == {"type": "string"}
    assert parameters["properties"]["top_k"] == {"type": "integer"}
    assert parameters["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
