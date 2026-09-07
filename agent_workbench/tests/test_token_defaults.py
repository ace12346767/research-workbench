from dataclasses import replace

from agent_workbench.config import AppConfig
from agent_workbench.providers import reasoning
from agent_workbench.runtime import ApplicationRuntime


def test_model_defaults_and_explicit_values():
    config = AppConfig(provider_mode='openai-compatible', model='deepseek-v4-flash-0731')
    budget = ApplicationRuntime.context_budget(config)
    assert (budget.window, budget.output_tokens) == (1000000, 32000)
    explicit = config.model_copy(update={'context_window': 131072, 'max_output_tokens': 65536})
    assert ApplicationRuntime.context_budget(explicit).output_tokens == 65536
    assert ApplicationRuntime.context_budget(explicit).window == 131072
    assert config.max_output_tokens is None


def test_unknown_model_and_small_context_have_conservative_defaults():
    config = AppConfig(provider_mode='openai-compatible', model='custom-unknown')
    budget = ApplicationRuntime.context_budget(config)
    assert (budget.window, budget.output_tokens) == (32768, 8192)
    small = ApplicationRuntime.context_budget(config.model_copy(update={'context_window': 4096}))
    assert small.output_tokens == 1024


def test_default_output_respects_model_limit(monkeypatch):
    monkeypatch.setitem(reasoning.PROFILES, 'test-limited', replace(reasoning.UNKNOWN, context_window=32768, output_limit=4096))
    config = AppConfig(provider_mode='openai-compatible', model='test-limited')
    assert ApplicationRuntime.context_budget(config).output_tokens == 4096


def test_settings_report_resolved_values_without_persisting_defaults(tmp_path):
    from agent_workbench.tests.test_approval_flow import make_runtime
    from agent_workbench.providers.mock import MockProvider
    runtime = make_runtime(tmp_path, MockProvider())
    settings = runtime.get_settings()
    assert settings['max_output_tokens'] is None
    assert settings['effective_limits']['max_output_tokens'] == 8192
    assert settings['effective_limits']['context_window'] == 32768


def test_provider_and_context_use_the_same_resolved_output(tmp_path):
    from agent_workbench.tests.test_approval_flow import make_runtime
    runtime = make_runtime(tmp_path)
    config = AppConfig(provider_mode='openai-compatible', model='deepseek-v4-flash-0731')
    provider = runtime.make_provider(config, api_key='local-test-only')
    assert provider.max_output_tokens == runtime.context_budget(config).output_tokens == 32000
    explicit = config.model_copy(update={'max_output_tokens': 65536})
    assert runtime.make_provider(explicit, api_key='local-test-only').max_output_tokens == 65536
