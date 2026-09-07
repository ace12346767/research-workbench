"""Resolve unset token budgets without changing user configuration."""
from agent_workbench.core.conversation import DEFAULT_CONTEXT_WINDOW, DEFAULT_OUTPUT_TOKENS
from agent_workbench.providers.reasoning import UNKNOWN, model_profile


def effective_limits(config):
    profile = model_profile(config.model) if config.provider_mode != 'mock' else UNKNOWN
    window = config.context_window or profile.context_window or DEFAULT_CONTEXT_WINDOW
    target = min(32000, profile.output_limit) if profile.output_limit else DEFAULT_OUTPUT_TOKENS
    # Unset output budgets leave room for input even on smaller custom gateways.
    output = config.max_output_tokens or max(1, min(target, window // 4))
    return {'context_window': window, 'max_output_tokens': output}
