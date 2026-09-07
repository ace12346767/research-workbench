"""Download the pinned E5 ONNX weight, verifying the exact release checksum."""
import hashlib
import urllib.request
from pathlib import Path

REVISION = '761b726dd34fb83930e26aab4e9ac3899aa1fa78'
SHA256 = 'f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193'
URL = f'https://huggingface.co/Xenova/multilingual-e5-small/resolve/{REVISION}/onnx/model_quantized.onnx'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    target = Path(__file__).resolve().parents[1] / 'assets/models/multilingual-e5-small/onnx/model_quantized.onnx'
    if target.exists():
        if digest(target) != SHA256:
            raise RuntimeError('Existing model checksum differs; preserve it and investigate before replacing it.')
        print('E5 ONNX weight verified; no download needed.')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix('.download')
    try:
        with urllib.request.urlopen(URL, timeout=60) as response, partial.open('wb') as file:
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
        if digest(partial) != SHA256:
            raise RuntimeError('Downloaded model checksum mismatch; model was not installed.')
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    print('Pinned E5 ONNX weight downloaded and verified.')


if __name__ == '__main__':
    main()
