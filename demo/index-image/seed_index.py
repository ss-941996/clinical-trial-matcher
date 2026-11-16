"""Verify the versioned trial index and initialize a shared volume once."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

CHUNK = 4 * 1024 * 1024


def verify_file(path: Path, manifest: dict) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Expected a regular index file: {path}')
    if path.stat().st_size != manifest['bytes']:
        raise ValueError(f'Index size mismatch: {path}; existing data is never overwritten')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    if digest.hexdigest() != manifest['sha256']:
        raise ValueError(f'Index checksum mismatch: {path}; existing data is never overwritten')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        records = db.execute("SELECT value FROM metadata WHERE key='records'").fetchone()
        if not records or json.loads(records[0]) != manifest['records']:
            raise ValueError('Index record-count metadata mismatch')
        db.execute('SELECT rowid FROM trials LIMIT 1').fetchone()
        db.execute('SELECT trial_id FROM identities LIMIT 1').fetchone()


def seed(source: Path, destination: Path, manifest: dict) -> str:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / 'bm25.sqlite'
    if target.exists() or target.is_symlink():
        verify_file(target, manifest)
        return 'Reused verified index; no copy needed.'
    # Copy to a unique temporary file. Publish only fully verified bytes, using
    # an atomic hard link that cannot replace an existing file or racing seeder.
    descriptor, name = tempfile.mkstemp(prefix='.index-', suffix='.partial', dir=destination)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as writer, source.open('rb') as reader:
            for block in iter(lambda: reader.read(CHUNK), b''):
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        verify_file(temporary, manifest)
        temporary.chmod(0o444)
        try:
            os.link(temporary, target)
        except FileExistsError:
            verify_file(target, manifest)
        return 'Initialized verified index volume.'
    finally:
        if temporary.exists():
            # Windows needs a writable bit for cleanup; Linux unlink ignores it.
            if os.name == 'nt':
                temporary.chmod(0o644)
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/payload/bm25.sqlite'))
    parser.add_argument('--manifest', type=Path, default=Path(__file__).with_name('manifest.json'))
    parser.add_argument('--destination', type=Path, default=Path('/data'))
    parser.add_argument('--verify-source', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    expected = os.environ.get('EXPECTED_INDEX_SHA256')
    if expected and expected != manifest['sha256']:
        raise ValueError('Data image differs from the Compose-pinned dataset version')
    if args.verify_source:
        verify_file(args.source, manifest)
        print('Source index verified.', flush=True)
    else:
        print(seed(args.source, args.destination, manifest), flush=True)


if __name__ == '__main__':
    main()
