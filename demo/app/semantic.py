"""Offline MedCPT query inference and exact search over a verified vector bundle."""
import hashlib
import json
from pathlib import Path


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class SemanticIndex:
    def __init__(self, directory, index_path):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.directory = Path(directory).resolve()
        manifest = json.loads((self.directory / 'manifest.json').read_text())
        if (manifest.get('schema_version') != 1 or type(manifest.get('dimensions')) is not int or manifest['dimensions']<1
                or manifest.get('similarity') != 'dot_product'
                or type(manifest.get('query_max_tokens')) is not int or not 2<=manifest['query_max_tokens']<=512):
            raise RuntimeError('Unsupported MedCPT vector bundle')
        if digest(index_path) != manifest['index_sha256']:
            raise RuntimeError('Lexical index and vector bundle do not match')
        for name, checksum in manifest['files'].items():
            path = (self.directory / name).resolve()
            if not path.is_relative_to(self.directory) or digest(path) != checksum:
                raise RuntimeError('Corrupt or unsafe MedCPT bundle file')
        ids = []
        self.shards = []
        for name in manifest['shards']:
            if name not in manifest['files']:
                raise RuntimeError('Unverified vector shard')
            with np.load(self.directory / name, allow_pickle=False) as shard:
                shard_ids = list(shard['ids'])
                vectors = shard['vectors'].copy()
            if vectors.shape != (len(shard_ids), manifest['dimensions']) or not np.isfinite(vectors).all():
                raise RuntimeError('Invalid MedCPT vectors')
            ids.extend(shard_ids)
            self.shards.append((shard_ids, vectors))
        if len(ids) != manifest['records'] or len(set(ids)) != len(ids):
            raise RuntimeError('Invalid MedCPT identities')
        self.ids = set(ids)
        self.manifest = manifest
        for required in ['query/tokenizer.json', 'query/encoder-int8.onnx']:
            if required not in manifest['files']:
                raise RuntimeError('Unverified query encoder')
        self.tokenizer = Tokenizer.from_file(str(self.directory / 'query/tokenizer.json'))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(str(self.directory / 'query/encoder-int8.onnx'),
            sess_options=options, providers=['CPUExecutionProvider'])
        shape=self.model.get_outputs()[0].shape
        if len(shape)!=2 or isinstance(shape[-1],int) and shape[-1]!=manifest['dimensions']:
            raise RuntimeError('Query model and trial vector dimensions differ')

    def search(self, note, count=1000):
        import numpy as np

        tok = self.tokenizer
        ids = [tok.token_to_id('[CLS]')] + tok.encode(note, add_special_tokens=False).ids[:self.manifest['query_max_tokens']-2] + [tok.token_to_id('[SEP]')]
        inputs = {'input_ids': np.asarray([ids], dtype=np.int64),
                  'attention_mask': np.ones((1, len(ids)), dtype=np.int64),
                  'token_type_ids': np.zeros((1, len(ids)), dtype=np.int64)}
        query = self.model.run(None, inputs)[0][0]
        if query.shape != (self.manifest['dimensions'],) or not np.isfinite(query).all():
            raise RuntimeError('Invalid patient query vector')
        best = []
        for trial_ids, vectors in self.shards:
            scores = vectors @ query
            if not np.isfinite(scores).all():
                raise RuntimeError('Invalid semantic similarity scores')
            # Shards are small; deterministic ordering also resolves boundary ties.
            hits = sorted(zip(trial_ids, map(float, scores)), key=lambda x: (-x[1], x[0]))[:count]
            best = sorted(best + hits, key=lambda x: (-x[1], x[0]))[:count]
        return best
