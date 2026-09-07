from __future__ import annotations

import asyncio
import copy
import time
from contextlib import aclosing

import httpx

from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider


PROBE_TOOL = {'type': 'function', 'function': {
    'name': 'diagnostic_echo', 'description': 'A diagnostic schema; no tool will be executed.',
    'parameters': {'type': 'object', 'properties': {'value': {'type': 'string', 'enum': ['ping']}},
                   'required': ['value'], 'additionalProperties': False},
}}


def error_code(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return 'authentication_failed'
        if status == 404:
            return 'model_or_endpoint_not_found'
        if status == 429:
            return 'rate_limited'
        if status >= 500:
            return 'service_unavailable'
        return 'request_rejected'
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return 'timeout'
    if isinstance(exc, httpx.RequestError):
        return 'connection_failed'
    return 'invalid_response'


async def _stream_probe(provider, *, tool: bool) -> dict:
    prompt = ('Call diagnostic_echo once with value "ping". Do not answer with text.' if tool
              else 'Reply with exactly the word OK.')
    observed = dict(content=False, reasoning=False, usage=False, tool_calls=False)
    calls, finish = [], None
    async with aclosing(provider.stream([{'role': 'user', 'content': prompt}],
                                       [copy.deepcopy(PROBE_TOOL)] if tool else [], asyncio.Event())) as stream:
        async for chunk in stream:
            if chunk.type in {'content', 'reasoning'} and chunk.delta and chunk.delta.strip():
                observed[chunk.type] = True
            elif chunk.type == 'usage' and chunk.usage:
                observed['usage'] = True
            elif chunk.type == 'tool_call' and chunk.tool_call:
                observed['tool_calls'] = True
                calls.append(chunk.tool_call)
            elif chunk.type == 'finish':
                finish = chunk.finish_reason
    if finish == 'length':
        status, code = 'not_observed', 'output_limit'
    elif finish is None:
        status, code = 'failed', 'incomplete_stream'
    elif tool:
        if not calls:
            status, code = 'not_observed', 'tool_call_not_observed'
        elif (len(calls) == 1 and calls[0].name == 'diagnostic_echo'
              and calls[0].arguments == {'value': 'ping'} and finish == 'tool_calls'):
            status, code = 'passed', 'ok'
        else:
            status, code = 'failed', 'invalid_tool_call'
    else:
        status, code = ('passed', 'ok') if observed['content'] and finish == 'stop' else ('failed', 'empty_response')
    return dict(status=status, code=code, observed=observed)


async def diagnose_provider(provider, *, probe_timeout: float = 15.0) -> dict:
    if isinstance(provider, OpenAICompatibleProvider):
        provider = copy.copy(provider)
        provider.max_output_tokens = 128
        provider.retry_count = 0
    checks = []
    for capability in ('models', 'generation', 'tool_call'):
        if checks and checks[0]['code'] == 'authentication_failed':
            checks.append(dict(capability=capability, status='skipped', code='authentication_failed', latency_ms=0))
            continue
        started = time.monotonic()
        try:
            if capability == 'models':
                models = await asyncio.wait_for(provider.list_models(), timeout=probe_timeout)
                if not isinstance(models, list) or any(not isinstance(model, str) for model in models):
                    raise ValueError('invalid models list')
                result = dict(status='passed', code='ok', model_visible=getattr(provider, 'model', None) in models,
                              model_count=len(models))
            else:
                result = await asyncio.wait_for(_stream_probe(provider, tool=capability == 'tool_call'),
                                                timeout=probe_timeout)
        except Exception as exc:
            result = dict(status='failed', code=error_code(exc))
        checks.append(dict(capability=capability, latency_ms=int((time.monotonic() - started) * 1000), **result))
    return dict(checks=checks, max_generation_requests=2, output_limit=128)
