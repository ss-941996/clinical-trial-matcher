"""Measure retrieval on the frozen November 2026 recruiting trial bundle."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import random
from statistics import mean, median, quantiles
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
NOTES = Path(__file__).with_name("notes.json")
CONFIG = ROOT / "demo" / "app" / "config.json"
METHODS = ("bm25", "medcpt", "hybrid")
REPEATS = 3
SEED = 20261126
WARMUP = (
    "Synthetic adult with chronic tinnitus and sleep disruption.",
    "Adult with confirmed Wilson disease and persistent movement symptoms.",
    "Adult with atopic eye inflammation and recurring severe irritation.",
    "Older adult with a knee replacement and persistent postoperative stiffness.",
    "Adult with hereditary angioedema and recurring swelling episodes.",
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def peak_resident_bytes():
    if os.name != "nt":
        import resource

        units = 1 if sys.platform == "darwin" else 1024
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * units

    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("page_faults", wintypes.DWORD),
            *[(name, ctypes.c_size_t) for name in (
                "peak_working_set", "working_set", "peak_paged_pool",
                "paged_pool", "peak_nonpaged_pool", "nonpaged_pool",
                "pagefile", "peak_pagefile",
            )],
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    get_memory = kernel.K32GetProcessMemoryInfo
    get_memory.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD,
    ]
    get_memory.restype = wintypes.BOOL
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not get_memory(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.peak_working_set


def make_ranker(bundle, method, records, config, depth=10):
    sys.path.insert(0, str(ROOT / "demo"))
    import serve_hybrid  # Applies the local demo CPU affinity and thread limits.
    from app.retrieval import Engine, focused, opening, rank_fusion, search_fts
    from app.semantic import SemanticIndex

    index = bundle / "bm25.sqlite"
    if method == "medcpt":
        semantic = SemanticIndex(bundle, index)
        return lambda note: semantic.search(note, 1000)[:depth], None

    engine = Engine(
        index, expected_records=records,
        config={**config, "default_method": method},
        vector_dir=bundle if method == "hybrid" else None,
    )
    engine.load()

    def rank(note):
        # Match the production search steps, with a common cutoff before hydration.
        with closing(engine.connect()) as db:
            if method == "bm25":
                return search_fts(db, note)[:depth]

            lexical_inputs = [note, focused(note, engine.vocab), opening(note)]
            field_weighted = rank_fusion(
                [search_fts(db, query, 1000, True) for query in lexical_inputs], k=1000,
            )
            lexical = rank_fusion(
                [search_fts(db, note, 1000), field_weighted], k=1000,
                weights=[1.0, config["fusion_balance_weight"]],
            )
            dense = engine.semantic.search(note, 1000)
            return rank_fusion(
                [lexical, dense], k=200, weights=[1.0, config["semantic_rrf_weight"]],
            )[:depth]

    return rank, engine


def run_method(bundle, method, repetition, notes, records, config):
    started = time.perf_counter()
    rank, engine = make_ranker(bundle, method, records, config)
    load_seconds = time.perf_counter() - started
    for note in WARMUP:
        ranked = rank(note)
        if engine is not None:
            expected = [row["trial_id"] for row in engine.search(note, method)["results"]]
            if [trial_id for trial_id, _ in ranked] != expected:
                raise RuntimeError(f"{method} does not match production on a warm-up note")
    order = list(notes)
    random.Random(SEED + repetition).shuffle(order)
    measurements = []
    for topic in order:
        start_ns = time.perf_counter_ns()
        ranking = rank(topic["note"])
        elapsed_ns = time.perf_counter_ns() - start_ns
        ids = [trial_id for trial_id, _ in ranking]
        if len(ids) > 10 or len(ids) != len(set(ids)):
            raise RuntimeError(f"Invalid ranking for {topic['id']}")
        measurements.append(dict(id=topic["id"], elapsed_ns=elapsed_ns, top10=ids))
        if len(measurements) % 25 == 0:
            print(f"{method} repetition {repetition}: {len(measurements)}/{len(order)}", flush=True)
    return dict(
        method=method, repetition=repetition, load_seconds=load_seconds,
        peak_resident_bytes=peak_resident_bytes(), queries=measurements,
    )


def describe(runs):
    rankings = {}
    stats = {}
    for method in METHODS:
        method_runs = [run for run in runs if run["method"] == method]
        rankings[method] = {row["id"]: row["top10"] for row in method_runs[0]["queries"]}
        for run in method_runs[1:]:
            if rankings[method] != {row["id"]: row["top10"] for row in run["queries"]}:
                raise RuntimeError(f"{method} rankings changed between repetitions")

        latencies = [row["elapsed_ns"] / 1e6 for run in method_runs for row in run["queries"]]
        stats[method] = dict(
            observations=len(latencies), median_ms=median(latencies),
            p95_ms=quantiles(latencies, n=100, method="inclusive")[94],
            peak_mib=max(run["peak_resident_bytes"] for run in method_runs) / (1024 ** 2),
            median_load_s=median(run["load_seconds"] for run in method_runs),
            minimum_returned=min(map(len, rankings[method].values())),
            median_ms_by_repetition=[
                median(row["elapsed_ns"] / 1e6 for row in run["queries"])
                for run in method_runs
            ],
        )

    overlaps = {}
    for a, b in (("bm25", "medcpt"), ("bm25", "hybrid"), ("medcpt", "hybrid")):
        pairs = [(ranking, rankings[b][note]) for note, ranking in rankings[a].items()]
        shared = [len(set(left) & set(right)) for left, right in pairs]
        overlaps[f"{a}_vs_{b}"] = dict(
            mean_shared=mean(shared), median_shared=median(shared),
            minimum_shared=min(shared), maximum_shared=max(shared),
            histogram=dict(sorted(Counter(shared).items())),
            same_first=sum(bool(left and right and left[0] == right[0]) for left, right in pairs),
            exact_order=sum(left == right for left, right in pairs),
        )
    return dict(methods=stats, overlap=overlaps)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cpu", default=os.environ.get("PROCESSOR_IDENTIFIER", "unspecified"))
    parser.add_argument("--child", choices=METHODS)
    parser.add_argument("--repeat", type=int)
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    notes, config = read_json(NOTES), read_json(CONFIG)
    manifest = read_json(bundle / "manifest.json")
    if manifest["records"] != 65160 or manifest["dimensions"] != 768:
        raise ValueError("Expected the completed November 2026 MedCPT bundle")
    if len({note["id"] for note in notes}) != 100 or set(Counter(note["area"] for note in notes).values()) != {10}:
        raise ValueError("Expected 100 unique note IDs and ten notes per disease area")
    if len(notes) != 100 or len({note["note"] for note in notes}) != 100:
        raise ValueError("Expected 100 distinct synthetic notes")

    if args.child:
        result = run_method(bundle, args.child, args.repeat, notes, manifest["records"], config)
        write_json(output, result)
        return

    output.mkdir(parents=True, exist_ok=True)
    protocol = dict(
        note_count=len(notes), areas=dict(Counter(note["area"] for note in notes)),
        note_sha256=sha256(NOTES), bundle_manifest_sha256=sha256(bundle / "manifest.json"),
        index_sha256=manifest["index_sha256"], index_bytes=(bundle / "bm25.sqlite").stat().st_size,
        bundle_bytes=sum(file.stat().st_size for file in bundle.rglob("*") if file.is_file()),
        query_model_bytes=(bundle / "query" / "encoder-int8.onnx").stat().st_size,
        vector_shards_bytes=sum((bundle / shard).stat().st_size for shard in manifest["shards"]),
        cpu=args.cpu, logical_cpus=os.cpu_count(), python=sys.version.split()[0],
        platform=sys.platform, logical_cpu_affinity=1, numeric_threads=1,
        seed=SEED, repetitions=REPEATS, warmup_notes=WARMUP, methods=METHODS,
        fusion_balance_weight=config["fusion_balance_weight"],
        semantic_rrf_weight=config["semantic_rrf_weight"],
        timing="In-process ranking, including live query encoding when used. Excludes startup, result hydration and HTTP.",
        memory="Maximum of the separate worker process peak resident memory readings, including startup and warm-up.",
    )
    write_json(output / "protocol.json", protocol)
    runs = []
    for step in range(REPEATS):
        print(f"Benchmark repetition {step + 1}/{REPEATS}", flush=True)
        for method in METHODS[step:] + METHODS[:step]:
            path = output / f"run-{step + 1}-{method}.json"
            subprocess.run([
                sys.executable, __file__, "--bundle", str(bundle), "--output", str(path),
                "--child", method, "--repeat", str(step + 1),
            ], check=True)
            runs.append(read_json(path))
    write_json(output / "summary.json", describe(runs))
    print(f"Completed {sum(len(run['queries']) for run in runs)} timed searches.", flush=True)


if __name__ == "__main__":
    main()
