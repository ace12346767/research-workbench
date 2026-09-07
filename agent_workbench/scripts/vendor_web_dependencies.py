"""Fetch pinned upstream browser bundles and licenses, verifying npm SHA-512."""
import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path
from urllib.request import urlopen


PACKAGES = (
    ('lucide', '0.468.0', 'UFbgwji/ZnAV7iTTE4jujyTV7J95AILKyATDUrqOJrMcUGfXvGjw3c1mcuHZUX2oJfkrAGU9KoxkrLQk2jjtiA==',
     {'dist/umd/lucide.min.js': 'lucide.min.js', 'LICENSE': 'lucide.LICENSE'}),
    ('markdown-it', '15.0.1', '9/7gE95FNPkfUWrjJIoHZza2iLmuJlPD0UNMxPi7bxUrbCR525YZY0r+zyfes0dZI5ZZ/uNIXUJca0pJvtw41g==',
     {'dist/browser/markdown-it.umd.min.js': 'markdown-it.min.js', 'LICENSE': 'markdown-it.LICENSE'}),
    ('dompurify', '3.4.14', 'dVoH9z+MY+C9IilgGCk3YfFqjLi3fChm2OiKJMzh6axrJ5qwxqWaZamgmHrpv22CN/KdbZJuGEGgfQoL00LTdg==',
     {'dist/purify.min.js': 'purify.min.js', 'LICENSE': 'dompurify.LICENSE', 'LICENSE-MPL': 'dompurify.LICENSE-MPL'}),
)


def main():
    root = Path(__file__).resolve().parents[1] / 'assets' / 'web' / 'vendor'
    staged, manifest = {}, []
    for name, version, digest, files in PACKAGES:
        url = f'https://registry.npmjs.org/{name}/-/{name}-{version}.tgz'
        with urlopen(url, timeout=30) as response:
            content = response.read()
        actual = base64.b64encode(hashlib.sha512(content).digest()).decode('ascii')
        if actual != digest:
            raise ValueError(f'Upstream integrity mismatch: {name}')
        hashes = {}
        with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
            for member, filename in files.items():
                info = archive.getmember(f'package/{member}')
                if not info.isfile():
                    raise ValueError(f'Expected a regular archive member: {member}')
                data = archive.extractfile(info).read()
                staged[filename] = data
                hashes[filename] = hashlib.sha256(data).hexdigest()
        manifest.append(dict(name=name, version=version, url=url, integrity=f'sha512-{digest}', sha256=hashes))
    root.mkdir(parents=True, exist_ok=True)
    for filename, data in staged.items():
        (root / filename).write_bytes(data)
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
