"""Fetch the pinned, small ONNX model during image build or local setup."""
import argparse
import hashlib
import json
from pathlib import Path
from huggingface_hub import snapshot_download
from app.rerank import MODEL, REVISION, GRAPH

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('models'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    snapshot_download(MODEL, revision=REVISION, local_dir=str(args.output),
                      allow_patterns=['tokenizer.json', 'config.json', GRAPH])
    manifest = {'model': MODEL, 'revision': REVISION, 'files': {}}
    for name in ['tokenizer.json', 'config.json', GRAPH]:
        path = args.output / name
        manifest['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(f'Model ready: {args.output.resolve()}')
