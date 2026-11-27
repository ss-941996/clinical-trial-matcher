"""Score all three retrieval methods from a complete blinded top-200 pool."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import mean

from benchmark import METHODS, read_json, write_json


def dcg(grades):
    return sum(grade / math.log2(rank + 1) for rank, grade in enumerate(grades, 1))


def load_grades(path, expected):
    seen = set()
    grades = defaultdict(dict)
    ungraded = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            key = (row["note_id"], row["trial_id"])
            if key not in expected or key in seen:
                raise ValueError(f"Unexpected or duplicate pair: {key}")
            seen.add(key)
            grade = row["grade"]
            if grade is None:
                ungraded += 1
            elif type(grade) is not int or grade not in (0, 1, 2):
                raise ValueError(f"Invalid grade for {key}: {grade}")
            else:
                grades[key[0]][key[1]] = grade

    if seen != expected or ungraded:
        raise ValueError(f"Judging is incomplete: {len(expected - seen)} missing and {ungraded} ungraded pairs")
    return grades


def score(rankings, grades):
    per_note = {}
    for note_id, ranking in rankings.items():
        pool = grades[note_id]
        relevant = sum(grade == 2 for grade in pool.values())
        ideal = dcg(sorted(pool.values(), reverse=True)[:10])
        per_note[note_id] = {
            "ndcg@10": dcg([pool[trial] for trial in ranking[:10]]) / ideal if ideal else None,
            "pooled_recall@200": sum(pool[trial] == 2 for trial in ranking) / relevant if relevant else None,
        }

    macro = {}
    for metric in ("ndcg@10", "pooled_recall@200"):
        eligible = [values[metric] for values in per_note.values() if values[metric] is not None]
        macro[metric] = {"mean": mean(eligible) if eligible else None, "notes": len(eligible)}
    return {"macro": macro, "per_note": per_note}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rankings", type=Path, required=True)
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot", action="store_true", help="Score only the same ten notes as the blinded pilot")
    args = parser.parse_args()
    rankings = {
        method: read_json(args.rankings / f"top200-{method}.json")["rankings"]
        for method in METHODS
    }
    if args.pilot:
        pilot = set(read_json(args.rankings / "blinded-pilot-grades.json")["grades"])
        if len(pilot) != 10 or any(not pilot <= run.keys() for run in rankings.values()):
            parser.error("The saved pilot must contain ten notes present in all rankings")
        rankings = {
            method: {note_id: ids for note_id, ids in run.items() if note_id in pilot}
            for method, run in rankings.items()
        }
    expected = {(note_id, trial) for run in rankings.values() for note_id, ids in run.items() for trial in ids}
    try:
        grades = load_grades(args.grades, expected)
    except ValueError as exc:
        parser.error(str(exc))
    write_json(args.output, {method: score(run, grades) for method, run in rankings.items()})


if __name__ == "__main__":
    main()
