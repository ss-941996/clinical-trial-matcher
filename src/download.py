"""Download immutable benchmark assets; verify hashes on reuse and record provenance."""
from __future__ import annotations
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import xml.etree.ElementTree as ET
import zipfile
import requests
from src.common import ROOT, now, sha256, write_json

RAW = ROOT / "data/raw/trec2021"
MANIFEST = ROOT / "data/manifests/trec2021.json"
BASE = "https://www.trec-cds.org/2021_data/"
ASSETS = {
    "topics2021.xml": "https://trec.nist.gov/data/trials/topics2021.xml",
    "qrels2021.txt": "https://trec.nist.gov/data/trials/qrels2021.txt",
    **{f"ClinicalTrials.2021-04-27.part{i}.zip": BASE + f"ClinicalTrials.2021-04-27.part{i}.zip" for i in range(1, 6)},
}

# Reference fingerprints published by ir_datasets, independently of the local copies.
REFERENCE_MD5 = dict(zip([f"ClinicalTrials.2021-04-27.part{i}.zip" for i in range(1,6)], [
    "e12eb9a0d21452503b0ef8874c69f490", "f6986125506434887a162f144ca4d9a2",
    "9b7fb528b22edfcf4535154cc3d98111", "4fd98d209e7b62cee87af211c0c281f6", "a747f09ac5d4f3cd0cc75957ad9f32d8"]))

def reference_check(path, name=None):
    name = name or path.name
    if name in REFERENCE_MD5:
        with path.open("rb") as f:
            digest = hashlib.file_digest(f,"md5").hexdigest()
        if digest != REFERENCE_MD5[name]:
            raise ValueError(f"Reference checksum mismatch: {path.name}")

def validate(path, name=None):
    suffix = Path(name or path.name).suffix
    if suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            if not z.namelist() or not any(n.endswith(".xml") for n in z.namelist()):
                raise ValueError(f"No trial XML in {path}")
            # CRC is checked while each member is consumed during cleaning.
    elif suffix == ".xml":
        root = ET.parse(path).getroot()
        if not root.findall("topic"):
            raise ValueError("No topics found")
    else:
        lines = path.read_text().splitlines()
        if not lines or any(len(x.split()) != 4 or x.split()[3] not in {"0", "1", "2"} for x in lines):
            raise ValueError("Invalid qrels")

def download(item):
    name, url = item
    path = RAW / name
    sidecar = RAW / (name + ".receipt.json")
    if path.exists() and not sidecar.exists():
        validate(path)
        reference_check(path)
        if path.name not in REFERENCE_MD5:
            raise ValueError(f"Unregistered local asset: {name}; inspect before replacing")
        receipt = dict(url=url, file=path.relative_to(ROOT).as_posix(), bytes=path.stat().st_size,
                       sha256=sha256(path), registered_at=now(), acquisition="manually downloaded by owner",
                       reference_md5=REFERENCE_MD5[name])
        write_json(sidecar,receipt)
        print(f"Registered verified local archive {name}",flush=True)
        return receipt
    if path.exists() and sidecar.exists():
        import json
        receipt = json.loads(sidecar.read_text())
        if receipt["sha256"] == sha256(path):
            validate(path)
            receipt["file"] = path.relative_to(ROOT).as_posix()
            print(f"Verified cached {name}", flush=True)
            return receipt
        raise ValueError(f"Hash mismatch for existing {name}; inspect before replacing")
    partial = path.with_suffix(path.suffix + ".partial")
    for attempt in range(3):
        try:
            print(f"Downloading {name} (attempt {attempt + 1})", flush=True)
            with requests.get(url, stream=True, timeout=(30, 120)) as response:
                response.raise_for_status()
                expected = int(response.headers.get("Content-Length", 0))
                with partial.open("wb") as f:
                    for block in response.iter_content(1024 * 1024):
                        f.write(block)
                if expected and not response.headers.get("Content-Encoding") and partial.stat().st_size != expected:
                    raise ValueError("Incomplete download")
                # Validate format before publishing the completed download.
                if name.endswith(".zip"):
                    with zipfile.ZipFile(partial) as z:
                        if not z.namelist():
                            raise ValueError("Empty archive")
                validate(partial, name)
                reference_check(partial, name)
                partial.replace(path)
                receipt = dict(url=url, final_url=response.url, file=path.relative_to(ROOT).as_posix(),
                               bytes=path.stat().st_size, sha256=sha256(path), fetched_at=now(),
                               etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"))
                write_json(sidecar, receipt)
                print(f"Finished {name}: {receipt['bytes']:,} bytes", flush=True)
                return receipt
        except (requests.RequestException, OSError, ValueError, zipfile.BadZipFile):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--small-only", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    items = [(k, v) for k, v in ASSETS.items() if not args.small_only or not k.endswith(".zip")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(download, items))
    write_json(MANIFEST, dict(dataset="TREC Clinical Trials 2021", snapshot="2021-04-27", year=2021,
        created_at=now(), scope="topics and qrels only" if args.small_only else "official full snapshot",
        source_page="https://www.trec-cds.org/2021.html", qrels_page="https://trec.nist.gov/data/trials2021.html",
        terms="https://clinicaltrials.gov/about-site/terms-conditions",
        citation="Roberts et al. Overview of the TREC 2021 Clinical Trials Track. TREC 2021.",
        integrity="Local SHA-256 fingerprints, not publisher-signed checksums. ZIP member CRC verified during parsing. Manual archives checked against ir_datasets MD5 references.",
        reference_checksums="https://github.com/allenai/ir_datasets/blob/master/ir_datasets/etc/downloads.json",
        files=sorted(receipts, key=lambda r:r["file"])))

if __name__ == "__main__":
    main()
