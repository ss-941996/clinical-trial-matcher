"""Pin Docker inputs to the completed recruiting release. Run from any directory."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    manifest=json.loads((ROOT/'data/manifests/ctgov-current.json').read_text(encoding='utf-8'))
    if manifest['serving_statuses']!=['RECRUITING']:raise ValueError('Expected recruiting-only release')
    index=ROOT/manifest['index']
    if not index.is_file() or index.stat().st_size!=manifest['bytes']:raise ValueError('Build the current index first')
    version=manifest['dataset_id']+'-'+manifest['sha256'][:12]
    text=f'''name: clinical-trial-matcher-images
services:
  trial-index:
    image: clinical-trial-index:{version}
    build:
      context: ..
      dockerfile: demo/index-image/Dockerfile
      args:
        INDEX_PATH: {Path(manifest['index']).as_posix()}
        INDEX_MANIFEST_PATH: data/manifests/ctgov-current.json
    environment:
      EXPECTED_INDEX_SHA256: {manifest['sha256']}
    volumes:
      - type: volume
        source: trial-index
        target: /data
        volume:
          nocopy: true
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    network_mode: none
    restart: "no"
  matcher:
    image: clinical-trial-matcher:local
    build:
      context: .
    depends_on:
      trial-index:
        condition: service_completed_successfully
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      TRIAL_INDEX_PATH: /data/bm25.sqlite
      DEMO_USERNAME: ${{DEMO_USERNAME:-}}
      DEMO_PASSWORD: ${{DEMO_PASSWORD:-}}
      REQUIRE_AUTH: ${{REQUIRE_AUTH:-false}}
      MAX_SEARCHES_PER_MINUTE: 10
    volumes:
      - type: volume
        source: trial-index
        target: /data
        read_only: true
        volume:
          nocopy: true
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    cpus: 0.5
    mem_limit: 1g
    init: true
    stop_grace_period: 60s
    restart: unless-stopped
volumes:
  trial-index:
    name: clinical-trial-index-{version}
'''
    (ROOT/'demo/compose.images.yaml').write_text(text,encoding='utf-8')
    bind=text[text.index('  matcher:'):text.index('volumes:\n  trial-index:')]
    # Keep the bind alternative explicit and independent of index initialization.
    bind=bind.replace('    depends_on:\n      trial-index:\n        condition: service_completed_successfully\n','')
    start=bind.index('    volumes:');end=bind.index('    read_only: true\n    tmpfs:',start)
    bind=bind[:start]+f'''    volumes:
      - type: bind
        source: ../{index.parent.relative_to(ROOT).as_posix()}
        target: /data
        read_only: true
        bind:
          create_host_path: false
'''+bind[end:]
    (ROOT/'demo/compose.yaml').write_text('services:\n'+bind,encoding='utf-8')
    parents=list(Path(manifest['index']).parents)
    patterns=['*','!demo/','!demo/index-image/','!demo/index-image/**']
    patterns+=['!'+p.as_posix()+'/' for p in reversed(parents) if p.as_posix()!='.']
    patterns+=['!'+Path(manifest['index']).as_posix(),'!data/manifests/','!data/manifests/ctgov-current.json']
    (ROOT/'demo/index-image/Dockerfile.dockerignore').write_text('\n'.join(patterns)+'\n',encoding='utf-8')
    print(f'Pinned {manifest["records"]:,} recruiting trials: clinical-trial-index:{version}')

if __name__=='__main__':main()
