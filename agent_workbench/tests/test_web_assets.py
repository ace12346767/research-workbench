import hashlib
import json
from pathlib import Path


def test_offline_browser_bundles_and_licenses_match_recorded_checksums():
    root = Path(__file__).resolve().parents[1] / 'assets' / 'web' / 'vendor'
    packages = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    assert packages
    for package in packages:
        assert package['integrity'].startswith('sha512-')
        assert any(name.endswith('.js') for name in package['sha256'])
        assert any('LICENSE' in name for name in package['sha256'])
        for filename, expected in package['sha256'].items():
            assert Path(filename).name == filename
            assert hashlib.sha256((root / filename).read_bytes()).hexdigest() == expected
