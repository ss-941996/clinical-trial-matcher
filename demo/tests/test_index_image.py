import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest

spec = importlib.util.spec_from_file_location('seed_index', Path(__file__).parents[1] / 'index-image/seed_index.py')
seed_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed_index)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'source.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE metadata(key TEXT,value TEXT)')
        db.execute('INSERT INTO metadata VALUES (?,?)', ('records', json.dumps(1)))
        db.execute('CREATE VIRTUAL TABLE trials USING fts5(title)')
        db.execute("INSERT INTO trials VALUES ('Research trial')")
        db.execute('CREATE TABLE identities(trial_id TEXT)')
        db.execute("INSERT INTO identities VALUES ('NCT00000001')")
    manifest = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'records': 1}
    return path, manifest


def test_seed_then_reuse_without_source_or_rewriting(source, tmp_path):
    path, manifest = source
    volume = tmp_path / 'volume'
    assert 'Initialized' in seed_index.seed(path, volume, manifest)
    target = volume / 'bm25.sqlite'
    before = target.stat().st_mtime_ns
    assert 'Reused' in seed_index.seed(tmp_path / 'missing-source', volume, manifest)
    assert target.stat().st_mtime_ns == before
    assert hashlib.sha256(target.read_bytes()).hexdigest() == manifest['sha256']


def test_corrupt_source_not_published(source, tmp_path):
    path, manifest = source
    manifest['sha256'] = '0' * 64
    volume = tmp_path / 'volume'
    with pytest.raises(ValueError, match='checksum mismatch'):
        seed_index.seed(path, volume, manifest)
    assert not (volume / 'bm25.sqlite').exists()
    assert not list(volume.glob('*.partial'))


def test_wrong_existing_index_is_not_overwritten(source, tmp_path):
    path, manifest = source
    volume = tmp_path / 'volume'
    volume.mkdir()
    target = volume / 'bm25.sqlite'
    target.write_bytes(b'existing data')
    with pytest.raises(ValueError, match='size mismatch'):
        seed_index.seed(path, volume, manifest)
    assert target.read_bytes() == b'existing data'
