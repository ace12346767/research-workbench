"""Optional offline routing run. Never called by prepare.py; no answer API calls."""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from agent_workbench.router.embedder import create_default_effort_router
from agent_workbench.router.settings import GuidanceConfig


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def threshold(value):
    result = float(value)
    if not 0 <= result <= 1:
        raise argparse.ArgumentTypeError('Threshold must be finite and in [0, 1]')
    return result


async def main(args):
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['frozen_files'].items():
        assert digest(HERE / name) == expected, f'Frozen corpus changed: {name}'
    for name, expected in manifest['implementation_hashes'].items():
        assert digest(REPO / name) == expected, f'Frozen implementation changed: {name}'
    if not args.run_id.replace('-', '').replace('_', '').isalnum():
        raise ValueError('Use an alphanumeric run ID, with optional hyphens or underscores')
    if args.split == 'validation' and not args.confirm_validation:
        raise ValueError('Reserve validation for chosen thresholds; add --confirm-validation explicitly')
    output = HERE / 'runs' / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    config = GuidanceConfig.model_validate_json((HERE / 'guidance.frozen.json').read_text(encoding='utf-8'))
    cases = json.loads((HERE / (args.split + '.json')).read_text(encoding='utf-8'))
    report = {'status': 'running', 'split': args.split, 'run_id': args.run_id,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'thresholds': {'min_score': args.min_score, 'min_margin': args.min_margin},
              'environment': {'python': sys.version, 'platform': platform.platform(),
                              'onnxruntime': importlib.metadata.version('onnxruntime')},
              'corpus_manifest_sha256': digest(HERE / 'manifest.json'),
              'provider_calls': 0, 'method': 'offline production router, not UI or answer quality validation', 'results': []}

    def save():
        (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    save()
    try:
        router = create_default_effort_router(REPO / 'agent_workbench/assets/models/multilingual-e5-small')
        router.configure(config)
        router.semantic_router.min_score = args.min_score
        router.semantic_router.min_margin = args.min_margin
        await router.semantic_router.warmup()
        assert router.semantic_router.state == 'ready', 'Real E5 must be ready'
        for case in cases:
            started = time.perf_counter()
            decision = await router.route(case['text'])
            technical_failure = decision.reason in {'timeout', 'error', 'unavailable', 'cold', 'router_error'}
            matched = decision.effort == case['expected'] and not technical_failure
            outcome = ('technical_failure' if technical_failure else 'correct' if matched else
                       'false_trigger' if case['expected'] is None else
                       'miss' if decision.effort is None else 'wrong_tier')
            report['results'].append({**case, 'route': decision.model_dump(), 'matched': matched,
                'outcome': outcome, 'elapsed_ms': (time.perf_counter() - started) * 1000})
            save()
        counts = Counter(r['outcome'] for r in report['results'])
        predicted = sum(r['route']['effort'] is not None and r['outcome'] != 'technical_failure' for r in report['results'])
        correct_triggers = sum(r['matched'] and r['expected'] is not None for r in report['results'])
        report['summary'] = {
            'total': len(cases), 'outcomes': dict(counts),
            'coverage': predicted / len(cases),
            'trigger_precision': correct_triggers / predicted if predicted else None,
            'false_trigger_denominator': sum(c['expected'] is None for c in cases),
            'positive_denominator': sum(c['expected'] is not None for c in cases),
        }
        report['status'] = 'complete'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = str(exc)
        raise
    finally:
        save()
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--split', choices=['calibration', 'validation'], default='calibration')
    parser.add_argument('--min-score', type=threshold, required=True)
    parser.add_argument('--min-margin', type=threshold, required=True)
    parser.add_argument('--confirm-validation', action='store_true')
    asyncio.run(main(parser.parse_args()))
