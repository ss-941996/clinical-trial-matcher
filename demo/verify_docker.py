"""Exercise the two-image demo, restart it, and verify persistent index reuse."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
COMPOSE = ['docker', 'compose', '-f', str(HERE/'compose.images.yaml')]


def command(*args):
    return subprocess.check_output([*args], cwd=HERE, text=True, timeout=600).strip()


def compose(*args):
    return command(*COMPOSE, *args)


def ready():
    started = time.perf_counter()
    while time.perf_counter()-started < 180:
        try:
            with urlopen('http://127.0.0.1:8000/health/ready', timeout=5) as response:
                if json.load(response)['status'] == 'ready':
                    return round(time.perf_counter()-started, 3)
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('App readiness timed out')


def search(method):
    payload = json.dumps(dict(note='A 54-year-old woman has type 2 diabetes treated with metformin. Her recent HbA1c is 8.1%. She has hypertension treated with lisinopril. Kidney function, smoking history, and prior trial participation are unknown.', method=method, top_k=10)).encode()
    with urlopen(Request('http://127.0.0.1:8000/match', data=payload, headers={'Content-Type':'application/json'}), timeout=240) as response:
        data = json.load(response)
    assert data['candidate_count'] == 200 and len(data['results']) == 10
    assert all(row['eligibility_status'] == 'not_assessed' and row['eligibility_text'] for row in data['results'])
    return dict(seconds=data['seconds'], candidates=data['candidate_count'], scored_pairs=data['rerank_scored_pairs'], ids=[row['trial_id'] for row in data['results']])


def identity():
    container = compose('ps', '-q', 'matcher')
    info = json.loads(command('docker', 'inspect', container))[0]
    probe = "import hashlib,json,pathlib; p=pathlib.Path('/data/bm25.sqlite'); s=p.stat(); h=hashlib.file_digest(p.open('rb'),'sha256').hexdigest(); print(json.dumps(dict(bytes=s.st_size,mtime_ns=s.st_mtime_ns,inode=s.st_ino,sha256=h)))"
    index = json.loads(compose('exec', '-T', 'matcher', 'python', '-c', probe))
    volume = next(m['Name'] for m in info['Mounts'] if m['Destination'] == '/data')
    assert index['sha256'] == 'b70ca9cdb12e5c4e709f767b25a8798aa56e2dc1102ec80722d3211f6669b1cd'
    return dict(container=container, image=info['Image'], volume=volume, index=index)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = {}
    compose('config', '--quiet')
    started = time.perf_counter()
    compose('up', '-d', '--no-build', '--pull', 'never')
    report['compose_up_and_readiness_seconds'] = round(time.perf_counter()-started+ready(), 3)
    report['seed_log'] = compose('logs', '--no-color', 'trial-index')
    report['before'] = identity()
    report['search'] = {method: search(method) for method in ['bm25', 'fusion', 'rerank']}
    compose('restart', 'matcher')
    report['restart_ready_seconds'] = ready()
    report['after_restart'] = identity()
    assert report['after_restart'] == report['before']
    assert search('bm25')['ids'] == report['search']['bm25']['ids']
    # Replace only the app container, exactly as for an app-only image update.
    compose('up', '-d', '--no-build', '--pull', 'never', '--no-deps', '--force-recreate', 'matcher')
    report['replacement_ready_seconds'] = ready()
    report['after_app_replacement'] = identity()
    assert report['before']['container'] != report['after_app_replacement']['container']
    for key in ['volume', 'index']:
        assert report['before'][key] == report['after_app_replacement'][key]
    assert search('fusion')['ids'] == report['search']['fusion']['ids']
    report['replacement_scope'] = 'App container recreated from same image; tests update procedure, not a new application version.'
    # A full Compose restart runs the initializer again and must reuse existing bytes.
    compose('up', '-d', '--no-build', '--pull', 'never')
    ready()
    report['reuse_seed_log'] = compose('logs', '--no-color', 'trial-index')
    assert 'Reused verified index; no copy needed.' in report['reuse_seed_log']
    after = identity()
    assert after['index'] == report['before']['index'] and after['volume'] == report['before']['volume']
    report['passed'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
