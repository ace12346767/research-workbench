"""Validate and freeze the corpus without embedding or running any routing cases."""
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from agent_workbench import __version__
from agent_workbench.router.effort_router import resolved_hits
from agent_workbench.router.request_text import request_intent
from agent_workbench.router.semantic_router import DEFAULT_MIN_MARGIN
from agent_workbench.router.settings import GuidanceConfig


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    if (HERE / 'manifest.json').exists():
        raise RuntimeError('Already frozen. Do not overwrite; a changed corpus requires a new version.')
    cases = json.loads((HERE / 'cases.json').read_text(encoding='utf-8'))
    assert len(cases) == len({c['id'] for c in cases}) == len({c['text'] for c in cases}) == 100
    assert Counter(c['expected'] for c in cases) == {None: 40, 'fast': 20, 'standard': 20, 'deep': 20}
    assert Counter(c['split'] for c in cases) == {'calibration': 80, 'validation': 20}
    config = GuidanceConfig()
    anchors = {text for group in config.anchor_groups() for text in group['examples']}
    for case in cases:
        assert set(case) == {'id', 'group', 'split', 'expected', 'text', 'basis'}
        assert case['text'].strip() and case['basis'].strip()
        assert case['text'] not in anchors, case['id']
        # Syntax audit only: do not let explicit effort rules inflate semantic scores.
        assert not resolved_hits(request_intent(case['text']).windows), case['id']
    groups = sorted({c['group'] for c in cases})
    assert len(groups) == 10
    for group in groups:
        assert Counter(c['split'] for c in cases if c['group'] == group) == {'calibration': 8, 'validation': 2}
    save('guidance.frozen.json', config.model_dump())
    for split in ('calibration', 'validation'):
        save(split + '.json', [c for c in cases if c['split'] == split])
    source_names = ['router/settings.py', 'router/effort_router.py', 'router/request_text.py',
                    'router/intent.py', 'router/semantic_router.py', 'router/embedder.py']
    model = REPO / 'agent_workbench/assets/models/multilingual-e5-small'
    tracked = [REPO / 'agent_workbench' / p for p in source_names]
    tracked += [model / p for p in ('config.json', 'tokenizer.json', 'onnx/model_quantized.onnx')]
    manifest = {
        'corpus_version': 'restrained-v1', 'app_version': __version__,
        'prepared_at': datetime.now(timezone.utc).isoformat(), 'status': 'prepared_not_run',
        'cases': 100, 'splits': {'calibration': 80, 'validation': 20},
        'baseline_thresholds': {'min_score': .52, 'min_margin': DEFAULT_MIN_MARGIN},
        'allowed_tuning': ['min_score', 'min_margin'], 'top_k': 3,
        'frozen_files': {name: digest(HERE / name) for name in ('cases.json', 'guidance.frozen.json', 'calibration.json', 'validation.json')},
        'implementation_hashes': {str(p.relative_to(REPO)).replace('\\', '/'): digest(p) for p in tracked},
        'labels': 'Human-authored product preference expectations, not objective task complexity labels.',
        'holdout': 'Prepared by the same author; reserved from tuning, not blind independent external validation.',
        'threshold_limits': 'Global thresholds cannot repair parsing, exclusions, explicit rules, or incorrectly ranked semantics.',
    }
    save('manifest.json', manifest)
    with (HERE / 'cases.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['id', 'group', 'split', 'expected', 'text', 'basis'])
        writer.writerows([c['id'], c['group'], c['split'], c['expected'] or 'abstain', c['text'], c['basis']] for c in cases)
    lines = ['# 克制型路由固定用例 v1', '', '状态：已准备，尚未执行。不要将本清单作为通过报告。', '',
             '80 条用于调参，20 条仅用于选定阈值后的验证。示例、标签及划分已冻结。', '',
             '| 编号 | 分组 | 集合 | 预期 | 输入 | 预先依据 |', '| --- | --- | --- | --- | --- | --- |']
    for c in cases:
        lines.append('| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', '<br>') for x in
            [c['id'], c['group'], c['split'], c['expected'] or '不注入', c['text'], c['basis']]) + ' |')
    (HERE / 'cases.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'prepared_not_run', 'cases': 100, 'splits': manifest['splits'],
                      'explicit_effort_cases': 0, 'embedding_calls': 0, 'provider_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
