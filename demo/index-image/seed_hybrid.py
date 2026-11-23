"""Copy a completed hybrid serving bundle byte for byte into the app volume."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory


def verify_bundle(directory: Path) -> dict:
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    if manifest['schema_version'] != 1:
        raise ValueError('Unsupported serving bundle schema')

    files = manifest['files']
    required = {'query/encoder-int8.onnx', 'query/tokenizer.json', *manifest['shards']}
    if not manifest['shards'] or not required <= files.keys():
        raise ValueError('Incomplete hybrid serving bundle')

    for name, expected in {**files, 'bm25.sqlite': manifest['index_sha256']}.items():
        path = directory / name
        if not path.resolve().is_relative_to(directory) or path.is_symlink() or not path.is_file():
            raise ValueError(f'Invalid serving bundle file: {name}')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError(f'Serving bundle checksum mismatch: {name}')
    return manifest


def seed(source: Path, volume: Path) -> str:
    manifest = verify_bundle(source)
    target = volume / 'bundle'

    def verify_existing() -> str:
        if target.is_symlink() or (target / 'manifest.json').read_bytes() != (source / 'manifest.json').read_bytes():
            raise ValueError('Volume contains another bundle. Select a new index version and volume.')
        verify_bundle(target)
        return 'Reused verified hybrid bundle.'

    if target.exists() or target.is_symlink():
        return verify_existing()

    volume.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.hybrid-', dir=volume) as temporary:
        staging = Path(temporary) / 'bundle'
        for name in ('manifest.json', 'bm25.sqlite', *manifest['files']):
            output = staging / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, output)
            output.chmod(0o444)
        verify_bundle(staging)
        try:
            staging.rename(target)
        except OSError:
            if target.exists():
                return verify_existing()
            raise
    return 'Initialized verified hybrid bundle.'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-source', action='store_true')
    args = parser.parse_args()
    source = Path('/payload')
    if args.verify_source:
        verify_bundle(source)
        print('Verified hybrid image payload.', flush=True)
    else:
        print(seed(source, Path('/data')), flush=True)
