"""
Download every image listed in portraits_manifest.csv into met_portraits/images/.

Reads URLs straight from the CSV (no API re-fetch). Saves each file as
{objectID}.{ext}, skipping files that already exist so reruns are cheap.
"""

import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "met_portraits" / "portraits_manifest.csv"
OUT_DIR = ROOT / "met_portraits" / "images"

HEADERS = {"User-Agent": "met-portrait-downloader/1.0"}
TIMEOUT = 30
MAX_WORKERS = 10
USE_SMALL = True  # primaryImageSmall is web-large; flip to False for originals


def download_one(row):
    url = row["primaryImageSmall"] if USE_SMALL else row["primaryImage"]
    object_id = row["objectID"]
    if not url:
        return object_id, False, "no url"

    ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
    out_path = OUT_DIR / f"{object_id}{ext}"
    if out_path.exists():
        return object_id, True, "skipped (exists)"

    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        if r.status_code != 200:
            return object_id, False, f"http {r.status_code}"
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return object_id, True, str(out_path.name)
    except Exception as e:
        return object_id, False, str(e)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Downloading {len(rows)} images to {OUT_DIR}/")

    ok = fail = skipped = 0
    failures = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(download_one, row) for row in rows]
        for i, fut in enumerate(as_completed(futures), 1):
            object_id, success, info = fut.result()
            if success:
                if info.startswith("skipped"):
                    skipped += 1
                else:
                    ok += 1
            else:
                fail += 1
                failures.append((object_id, info))
            print(f"  [{i:>2}/{len(rows)}] {object_id}: {info}")

    print(f"\nDone. downloaded={ok}  skipped={skipped}  failed={fail}")
    if failures:
        print("\nFailures:")
        for object_id, reason in failures:
            print(f"  {object_id}: {reason}")
        sys.exit(1)


if __name__ == "__main__":
    main()
