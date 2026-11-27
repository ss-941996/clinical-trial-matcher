"""Export deeper rankings and create a blinded pool for recall@200."""
from collections import defaultdict
from contextlib import closing
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import random
import sqlite3

from benchmark import CONFIG, METHODS, NOTES, read_json, make_ranker, write_json


def export_rankings(bundle, output, notes, protocol):
    config = read_json(CONFIG)
    for weight in ("fusion_balance_weight", "semantic_rrf_weight"):
        if config[weight] != protocol[weight]:
            raise ValueError(f"The current {weight} differs from the saved benchmark")

    for method in METHODS:
        path = output / f"top200-{method}.json"
        previous = {
            pair["id"]: pair["top10"]
            for pair in read_json(output / f"run-1-{method}.json")["queries"]
        }
        if path.exists():
            rankings = read_json(path)["rankings"]
        else:
            rank, engine = make_ranker(bundle, method, 65160, config, depth=200)
            rankings = {}
            for note in notes:
                rankings[note["id"]] = [trial for trial, _ in rank(note["note"])]
                if len(rankings) % 25 == 0:
                    print(f"{method} top 200: {len(rankings)}/{len(notes)} notes", flush=True)
            del rank, engine

        if set(rankings) != set(previous):
            raise ValueError(f"{method}: note IDs differ from the saved benchmark")
        for note_id, ranking in rankings.items():
            if ranking[:10] != previous[note_id] or len(ranking) > 200 or len(ranking) != len(set(ranking)):
                raise ValueError(f"{method}: unexpected ranking for {note_id}")
        write_json(path, dict(method=method, depth=200, rankings=rankings))
        yield method, rankings


def build_pool(bundle, output, notes, rankings, pilot_grades):
    randomizer = random.Random(20261126)
    depths = defaultdict(dict)
    for method, queries in rankings.items():
        depths[method] = {note_id: len(ids) for note_id, ids in queries.items()}

    pair_count = 0
    pilot_pairs = 0
    existing_grades = 0
    trial_ids = set()
    pool_path = output / "blinded-top200-pool.jsonl"
    catalog_path = output / "top200-trial-catalog.jsonl.gz"
    with pool_path.with_suffix(".tmp").open("w", encoding="utf-8") as pool:
        for note in notes:
            note_id = note["id"]
            ids = sorted({trial for method in METHODS for trial in rankings[method][note_id]})
            randomizer.shuffle(ids)
            trial_ids.update(ids)
            pair_count += len(ids)
            if note_id in pilot_grades:
                pilot_pairs += len(ids)
            existing_grades += sum(trial in pilot_grades.get(note_id, {}) for trial in ids)
            for trial in ids:
                pool.write(json.dumps({"note_id": note_id, "trial_id": trial, "grade": None}) + "\n")

    index = (bundle / "bm25.sqlite").resolve()
    with closing(sqlite3.connect(index.as_uri() + "?mode=ro", uri=True)) as db:
        with gzip.open(catalog_path.with_suffix(".tmp"), "wt", encoding="utf-8", compresslevel=5) as catalog:
            for trial in sorted(trial_ids):
                row = db.execute(
                    "SELECT t.title, t.conditions, t.summary, t.eligibility "
                    "FROM identities i JOIN trials t ON t.rowid=i.row_id WHERE i.trial_id=?",
                    (trial,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Missing trial {trial}")
                catalog.write(json.dumps(dict(
                    trial_id=trial, title=row[0], conditions=row[1],
                    summary=row[2], eligibility=row[3],
                ), ensure_ascii=False) + "\n")

    catalog_path.with_suffix(".tmp").replace(catalog_path)
    pool_path.with_suffix(".tmp").replace(pool_path)
    write_json(output / "top200-coverage.json", dict(
        notes=len(notes), pair_count=pair_count, unique_trials=len(trial_ids),
        pilot_note_count=len(pilot_grades), pilot_pair_count=pilot_pairs,
        existing_top10_pilot_grades=existing_grades,
        minimum_returned={method: min(depths[method].values()) for method in METHODS},
        maximum_returned={method: max(depths[method].values()) for method in METHODS},
        top10_identical_to_saved_benchmark=True,
        recall200_status="Requires relevance judgments at the deeper pool depth.",
    ))


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    protocol = read_json(output / "protocol.json")
    for file, expected in (
        (NOTES, protocol["note_sha256"]),
        (bundle / "manifest.json", protocol["bundle_manifest_sha256"]),
        (bundle / "bm25.sqlite", protocol["index_sha256"]),
    ):
        if sha256(file) != expected:
            raise ValueError(f"Frozen benchmark checksum mismatch: {file}")
    notes = read_json(NOTES)
    rankings = dict(export_rankings(bundle, output, notes, protocol))
    pilot = read_json(output / "blinded-pilot-grades.json")["grades"]
    build_pool(bundle, output, notes, rankings, pilot)
    print("Top-200 rankings and blinded judging pool saved.", flush=True)


if __name__ == "__main__":
    main()
