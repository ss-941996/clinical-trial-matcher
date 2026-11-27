"""Prepare a blinded top-ten pool for the November retrieval benchmark."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "nov2026-retrieval"
NOTES = Path(__file__).with_name("notes.json")
METHODS = ("bm25", "medcpt", "hybrid")
SEED = 20261126


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main(index):
    protocol = read(REPORT / "protocol.json")
    if sha256(index) != protocol["index_sha256"] or sha256(NOTES) != protocol["note_sha256"]:
        raise ValueError("The SQLite index or notes differ from the frozen benchmark")
    notes = read(NOTES)
    ranked = {
        method: {
            row["id"]: row["top10"]
            for row in read(REPORT / f"run-1-{method}.json")["queries"]
        }
        for method in METHODS
    }
    expected = {note["id"] for note in notes}
    if any(set(results) != expected for results in ranked.values()):
        raise ValueError("The rankings and frozen notes have different IDs")

    from tempfile import NamedTemporaryFile

    dest = REPORT / "blinded-top10-pool.jsonl"
    if dest.exists():
        raise FileExistsError(f"Move the existing pool before regenerating it: {dest}")
    rng = random.Random(SEED)
    pairs = 0
    trials = set()
    with closing(sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=REPORT, delete=False) as out:
            temporary = Path(out.name)
            try:
                for note in notes:
                    ids = sorted({trial for method in METHODS for trial in ranked[method][note["id"]]})
                    rng.shuffle(ids)
                    for trial_id in ids:
                        row = db.execute(
                            "SELECT t.title, t.conditions, t.summary, t.eligibility "
                            "FROM identities i JOIN trials t ON t.rowid=i.row_id WHERE i.trial_id=?",
                            (trial_id,),
                        ).fetchone()
                        if row is None:
                            raise ValueError(f"Missing trial {trial_id}")
                        record = dict(
                            note_id=note["id"], note=note["note"], trial_id=trial_id,
                            title=row[0], conditions=row[1], summary=row[2], eligibility=row[3],
                            grade=None,
                        )
                        out.write(json.dumps(record, ensure_ascii=False) + "\n")
                        pairs += 1
                        trials.add(trial_id)
            except BaseException:
                out.close()
                temporary.unlink()
                raise
    temporary.replace(dest)
    print(f"{pairs} note-trial pairs; {len(trials)} distinct trial IDs; {len(notes)} notes.")
    print(dest)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
