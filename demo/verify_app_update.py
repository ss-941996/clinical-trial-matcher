"""After building/loading a new app tag, verify an app-only update reuses its index."""
import argparse
import json
from pathlib import Path
from verify_docker import compose, identity, ready, search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    before = identity()
    old_results = search('bm25')
    compose('up', '-d', '--no-build', '--pull', 'never', '--no-deps', '--force-recreate', 'matcher')
    seconds = ready()
    after = identity()
    assert before['container'] != after['container']
    assert before['image'] != after['image'], 'Build or load an updated app image before running this check'
    assert before['volume'] == after['volume'] and before['index'] == after['index']
    new_results = search('bm25')
    assert old_results['ids'] == new_results['ids']
    report = dict(before=before, after=after, readiness_seconds=seconds, bm25=new_results,
                  passed=True, scope='Real app-image change; existing data image and volume preserved. Timing includes competing local experiment processes.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
