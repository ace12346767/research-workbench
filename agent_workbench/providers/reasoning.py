"""Explicit model profiles; never infer protocol support from ordinal tier counts."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ReasoningProfile:
    policy: str
    levels: tuple[str, ...] = ()
    default: str | None = None
    mapping: tuple[str, str, str] | None = None
    completion_tokens: bool = False
    preserve_reasoning: bool = False
    explicit_thinking: bool = False
    context_window: int | None = None
    output_limit: int | None = None


DEEPSEEK_V4 = ReasoningProfile('mapped', ('low', 'high', 'max'), 'high', ('low', 'high', 'max'),
                               preserve_reasoning=True, explicit_thinking=True, context_window=1000000, output_limit=384000)
LEGACY_DEEPSEEK = ReasoningProfile('guidance_only', preserve_reasoning=True)
UNKNOWN = ReasoningProfile('unsupported')
# Deliberately bounded allowlist, including published date snapshots only.
PROFILES = {
    'gpt-5': ReasoningProfile('mapped', ('minimal', 'low', 'medium', 'high'), 'medium', ('low', 'medium', 'high'), True),
    'gpt-5-mini': ReasoningProfile('mapped', ('minimal', 'low', 'medium', 'high'), 'medium', ('low', 'medium', 'high'), True),
    'gpt-5-nano': ReasoningProfile('mapped', ('minimal', 'low', 'medium', 'high'), 'medium', ('low', 'medium', 'high'), True),
    'gpt-5.1': ReasoningProfile('mapped', ('none', 'low', 'medium', 'high'), 'none', ('low', 'medium', 'high'), True),
    'gpt-5.2': ReasoningProfile('mapped', ('none', 'low', 'medium', 'high', 'xhigh'), 'none', ('low', 'medium', 'xhigh'), True),
    'gpt-5.4': ReasoningProfile('mapped', ('none', 'low', 'medium', 'high', 'xhigh'), 'none', ('low', 'medium', 'xhigh'), True),
    'gpt-5.5': ReasoningProfile('mapped', ('none', 'low', 'medium', 'high', 'xhigh'), 'medium', ('low', 'medium', 'xhigh'), True),
    'o3': ReasoningProfile('mapped', ('low', 'medium', 'high'), 'medium', ('low', 'medium', 'high'), True),
    'o4-mini': ReasoningProfile('mapped', ('low', 'medium', 'high'), 'medium', ('low', 'medium', 'high'), True),
}

# Nominal published capacities; a custom gateway may impose a smaller limit.
from dataclasses import replace
PROFILES['gpt-5'] = replace(PROFILES['gpt-5'], output_limit=128000)
for _model in ('gpt-5', 'gpt-5-mini', 'gpt-5-nano', 'gpt-5.1', 'gpt-5.2'):
    PROFILES[_model] = replace(PROFILES[_model], context_window=400000)
for _model in ('gpt-5.4', 'gpt-5.5'):
    PROFILES[_model] = replace(PROFILES[_model], context_window=1050000)


def model_profile(model: str) -> ReasoningProfile:
    name = model.strip().casefold()
    # Model namespaces identify names, not arbitrary substring matches.
    name = re.sub(r'^(?:openai|deepseek|deepseek-ai)/', '', name)
    name = {'deepseek-v4-flash-0731': 'deepseek-v4-flash',
            'deepseek-v4-pro-0813': 'deepseek-v4-pro'}.get(name, name)
    if name in {'deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'}:
        return DEEPSEEK_V4
    if name.startswith('deepseek-'):
        return LEGACY_DEEPSEEK
    name = re.sub(r'-\d{4}-\d{2}-\d{2}$', '', name)
    return PROFILES.get(name, UNKNOWN)


def reasoning_capabilities(config) -> dict:
    profile = model_profile(config.model) if config.provider_mode != 'mock' else UNKNOWN
    mapping = dict(zip(('fast', 'standard', 'deep'), profile.mapping)) if profile.mapping else {}
    return dict(policy=profile.policy, levels=list(profile.levels), default=profile.default,
                context_window=profile.context_window,
                mapping=mapping, can_link=bool(mapping), profile_source='builtin_model_allowlist',
                endpoint_verified=False)


def normalize_config(config):
    result = config.model_copy(deep=True)
    capability = reasoning_capabilities(result)
    if not result.guidance.enabled or not capability['can_link']:
        result.guidance.link_reasoning = False
    if result.reasoning_effort not in capability['levels']:
        result.reasoning_effort = None
    return result


def effective_reasoning(config, effort: str | None) -> str | None:
    config = normalize_config(config)
    capability = reasoning_capabilities(config)
    if config.guidance.link_reasoning:
        return capability['mapping'].get(effort, capability['default'])
    return config.reasoning_effort
