"""
Met Museum portrait downloader.

Pipeline:
  1. Hit the Met search API with multiple portrait-related queries.
  2. Union the resulting object IDs (search returns dupes across queries).
  3. Fetch each object's metadata, keep only public-domain ones with a
     primaryImage URL.
  4. Optionally filter by classification (Paintings / Photographs / etc).
  5. Save a CSV manifest of matches and download the images.

Met API rate limit: 80 req/sec. We stay well under that.
Docs: https://metmuseum.github.io/
"""

import csv
import os
import sys
import time
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

API = "https://collectionapi.metmuseum.org/public/collection/v1"
HEADERS = {"User-Agent": "met-portrait-downloader/1.0"}
TIMEOUT = 30

# These queries cast a wide net. The Met tags portraits across classifications
# (Paintings, Photographs, Prints, Drawings, Sculpture, Miniatures...).
# Searching multiple variants and unioning the IDs catches more than any single
# query does — `q=portrait` alone misses things tagged only as "Portraits" or
# "self-portrait" in the title.
PORTRAIT_QUERIES = [
    {"q": "portrait", "hasImages": "true"},
    {"q": "portrait", "hasImages": "true", "title": "true"},
    {"q": "portrait", "hasImages": "true", "tags": "true"},
    {"q": "self-portrait", "hasImages": "true"},
    {"q": "self portrait", "hasImages": "true"},
    {"q": "portraiture", "hasImages": "true"},
    {"q": "bust", "hasImages": "true"},
    {"q": "head of", "hasImages": "true"},
    {"q": "sitter", "hasImages": "true"},
    {"q": "likeness", "hasImages": "true"},
    {"q": "miniature portrait", "hasImages": "true"},
    {"q": "portrait", "hasImages": "true", "isPublicDomain": "true"},
    {"q": "face", "hasImages": "true"},
    {"q": "effigy", "hasImages": "true"},
]

# If you only want paintings, set this to {"Paintings"}.
# Leave as None to keep all classifications.
ALLOWED_CLASSIFICATIONS = None  # e.g. {"Paintings", "Photographs"}


def search_ids(params, retries=5):
    """Run a search query, return the list of matching object IDs.
    Retries with backoff on rate limits / transient errors."""
    backoff = 2.0
    for attempt in range(retries):
        try:
            r = requests.get(f"{API}/search", params=params,
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json().get("objectIDs") or []
            if r.status_code == 404:
                return []
        except Exception:
            pass
        time.sleep(backoff)
        backoff *= 2
    return []


def collect_candidate_ids():
    """Run all portrait queries, union the IDs."""
    ids = set()
    for q in PORTRAIT_QUERIES:
        got = search_ids(q)
        print(f"  query {q} -> {len(got):,} hits")
        ids.update(got)
        time.sleep(0.5)  # be polite to the search endpoint
    print(f"\nUnique candidate object IDs across all queries: {len(ids):,}\n")
    return sorted(ids)


def fetch_object(object_id, retries=4):
    """Fetch one object record. Retries on transient failures (429/5xx/network).
    Returns the parsed dict, or None only if the object truly doesn't exist (404)."""
    backoff = 0.5
    for attempt in range(retries):
        try:
            r = requests.get(f"{API}/objects/{object_id}",
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            # 429 (rate limit) or 5xx — back off and retry
        except Exception:
            pass
        time.sleep(backoff)
        backoff *= 2
    return None


def is_portrait_record(obj):
    """Filter rule: public-domain, has an image, and (optionally) right class."""
    if not obj:
        return False
    if not obj.get("isPublicDomain"):
        return False
    if not obj.get("primaryImage"):
        return False
    if ALLOWED_CLASSIFICATIONS is not None:
        if obj.get("classification") not in ALLOWED_CLASSIFICATIONS:
            return False
    return True


def filter_to_portraits(ids, max_workers=10):
    """Fetch all candidate records in parallel, return the keepers."""
    keepers = []
    total = len(ids)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_object, oid): oid for oid in ids}
        for fut in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  filtered {done:,}/{total:,} "
                      f"({len(keepers):,} keepers so far)")
            obj = fut.result()
            if is_portrait_record(obj):
                keepers.append(obj)
    return keepers


def write_manifest(records, path):
    """Write a CSV with the fields most people care about."""
    fields = ["objectID", "title", "artistDisplayName", "objectDate",
              "classification", "medium", "department", "primaryImage",
              "primaryImageSmall", "objectURL"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow(r)
    print(f"Manifest written: {path}")


def download_image(rec, out_dir, use_small=True):
    """Download one image. Returns (objectID, ok, path_or_error)."""
    url = rec.get("primaryImageSmall" if use_small else "primaryImage")
    if not url:
        return rec["objectID"], False, "no url"
    ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
    out = out_dir / f"{rec['objectID']}{ext}"
    if out.exists():
        return rec["objectID"], True, str(out)
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        if r.status_code != 200:
            return rec["objectID"], False, f"http {r.status_code}"
        with open(out, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return rec["objectID"], True, str(out)
    except Exception as e:
        return rec["objectID"], False, str(e)


def download_all(records, out_dir, max_workers=10, use_small=True):
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(download_image, r, out_dir, use_small)
                   for r in records]
        for i, fut in enumerate(as_completed(futures), 1):
            _, success, _ = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
            if i % 100 == 0:
                print(f"  downloaded {i:,}/{len(records):,} "
                      f"(ok={ok:,} fail={fail:,})")
    print(f"\nDone. ok={ok:,} fail={fail:,}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="met_portraits",
                   help="Output directory (default: ./met_portraits)")
    p.add_argument("--count-only", action="store_true",
                   help="Just print the count, don't download images")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap number of images downloaded (for testing)")
    p.add_argument("--full-size", action="store_true",
                   help="Download original (large) images instead of web-large")
    args = p.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Step 1: searching the Met API for portrait candidates...")
    ids = collect_candidate_ids()

    print("Step 2: fetching each object's metadata and filtering "
          "to public-domain portraits with images...")
    t0 = time.time()
    keepers = filter_to_portraits(ids)
    print(f"\n=== RESULT ===")
    print(f"Public-domain portrait images available: {len(keepers):,}")
    print(f"Filtering took {time.time()-t0:.1f}s\n")

    manifest_path = out_dir / "portraits_manifest.csv"
    write_manifest(keepers, manifest_path)

    if args.count_only:
        return

    to_download = keepers if args.limit is None else keepers[:args.limit]
    print(f"\nStep 3: downloading {len(to_download):,} images "
          f"to {out_dir/'images'}/")
    download_all(to_download, out_dir / "images",
                 use_small=not args.full_size)


if __name__ == "__main__":
    main()
