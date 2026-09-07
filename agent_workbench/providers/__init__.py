"""Model provider adapters."""

from agent_workbench.providers.mock import MockProvider
from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider

__all__ = ["MockProvider", "OpenAICompatibleProvider"]
