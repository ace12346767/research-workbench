from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NonemptyExample = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=320)]
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class GuidanceProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    examples: list[NonemptyExample] = Field(min_length=1, max_length=12)
    prompt: Prompt


def default_profiles() -> dict:
    # Match expressed preferences, not subject matter or inferred task difficulty.
    scope = (" Do not expand the task or add unrelated checks. "
             "Follow the user's explicit requirements for scope, format, and length.")
    return {
        'fast': dict(examples=[
            '我只想确认这一点，其他的先不讨论。',
            '先告诉我最关键的结果，背景可以省略。',
            '我现在只需要一个能用的答案。',
            '抓住最必要的几项就够了，不用面面俱到。',
            'I only need to settle this one point; leave the rest aside.',
            'Start with the essential result; I do not need the background.',
        ], prompt='Handle this efficiently. Focus on the requested result, with only the explanation and checks needed to make it usable and reliable.' + scope),
        'standard': dict(examples=[
            '我没理解中间是怎么联系起来的。',
            '让我知道该怎么做，以及做完后怎么确认。',
            '把主要过程交代清楚，别跳过关键环节。',
            '除了结果，也说清楚为什么这么做。',
            'I do not understand how the intermediate parts connect.',
            'Help me understand what to do and how to tell whether it worked.',
        ], prompt='Explain the relevant connections or steps and the basis for the result. Include a proportionate check or example only where it helps complete the request.' + scope),
        'deep': dict(examples=[
            '这个结论依赖哪些没有说出来的前提？',
            '有没有反例，或者同样说得通的其他解释？',
            '这些因素一起变化时，会不会相互影响？',
            '换一种条件，原来的判断还成立吗？',
            '有没有遗漏的环节，会让整体结果发生变化？',
            'What unstated assumptions does this conclusion depend on?',
            'Could a counterexample or another explanation change the conclusion?',
        ], prompt="Check the assumptions, counterexamples, or interactions relevant to the user's question. Distinguish supported conclusions from uncertainty, and explain only the consequences that matter to the request." + scope),
    }


class GuidanceProfiles(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fast: GuidanceProfile = Field(default_factory=lambda: GuidanceProfile(**default_profiles()['fast']))
    standard: GuidanceProfile = Field(default_factory=lambda: GuidanceProfile(**default_profiles()['standard']))
    deep: GuidanceProfile = Field(default_factory=lambda: GuidanceProfile(**default_profiles()['deep']))


class GuidanceConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    link_reasoning: bool = False
    profiles: GuidanceProfiles = Field(default_factory=GuidanceProfiles)

    def anchor_groups(self) -> list[dict]:
        return [dict(effort=key, examples=list(getattr(self.profiles, key).examples))
                for key in ('fast', 'standard', 'deep')]
