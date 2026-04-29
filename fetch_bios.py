"""
Fetch a Wikipedia bio for each portrait listed in manifest.csv and save it
to wikimedia_portraits/bios/{slug}.json.

Each bio JSON is self-contained — it carries the image path, the source
URL, and all the bio fields — so it can be uploaded to a database row by
row without joins.

API: https://en.wikipedia.org/api/rest_v1/page/summary/{title}
Cache: bios/.bio_cache.json keyed by wikipedia_title.
"""

import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "wikimedia_portraits"
MANIFEST_PATH = DATA_DIR / "manifest.csv"
BIOS_DIR = DATA_DIR / "bios"
CACHE_PATH = BIOS_DIR / ".bio_cache.json"

WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
HEADERS = {
    "User-Agent": (
        "MetPortraitsResearch/1.0 "
        "(https://commons.wikimedia.org/wiki/Commons:Featured_pictures/Historical/People; "
        "personal research) python-requests/2.32"
    ),
    "Accept": "application/json",
}
TIMEOUT = 30
DELAY_SEC = 0.15
MAX_RETRIES = 3
INITIAL_BACKOFF_SEC = 4


def slug_from_image_path(image_path):
    """images/Edgar_A._Poe_-_NARA_-_528345__cropped.jpg -> Edgar_A._Poe_-_NARA_-_528345__cropped"""
    stem = os.path.splitext(os.path.basename(image_path))[0]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "unnamed"


def load_cache():
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_cache(cache):
    BIOS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def fetch_summary(title, cache):
    """Return the Wikipedia summary JSON for a title (cached). None on failure."""
    if title in cache:
        return cache[title]

    url = WIKI_SUMMARY_URL.format(quote(title.replace(" ", "_")))
    backoff = INITIAL_BACKOFF_SEC
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except Exception as e:
            cache[title] = None
            return None

        if r.status_code == 200:
            data = r.json()
            cache[title] = data
            time.sleep(DELAY_SEC)
            return data

        if r.status_code == 429 and attempt < MAX_RETRIES:
            wait = int(r.headers.get("Retry-After", "0")) or backoff
            time.sleep(wait)
            backoff *= 2
            continue

        cache[title] = None
        return None

    cache[title] = None
    return None


def shape_bio_record(row, summary):
    """Build the JSON record we save per person.

    Pulls a few core fields out of the Wikipedia summary into top-level keys,
    and also keeps the full raw summary under `wikipedia_summary` for any
    downstream use (LLM prompts, debugging).
    """
    extract = (summary or {}).get("extract", "")
    description = (summary or {}).get("description", "")
    canonical_title = (summary or {}).get("title", row.get("wikipedia_title", ""))
    page_url = (summary or {}).get("content_urls", {}).get("desktop", {}).get("page", "")
    if not page_url:
        page_url = f"https://en.wikipedia.org/wiki/{quote(canonical_title.replace(' ', '_'))}"

    return {
        "slug": slug_from_image_path(row["local_file"]),
        "name": canonical_title,
        "description": description,
        "summary": extract,
        "image_local": row["local_file"],
        "image_url": row["image_url"],
        "portrait_date": row["date"],
        "original_caption": row["original_caption"],
        "extracted_name": row["extracted_name"],
        "wikipedia_title": row["wikipedia_title"],
        "wikipedia_url": page_url,
        "commons_file_page": row["file_page"],
        "wikipedia_summary": summary,
    }


def write_bio(record):
    BIOS_DIR.mkdir(parents=True, exist_ok=True)
    path = BIOS_DIR / f"{record['slug']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return path


def main():
    if not MANIFEST_PATH.exists():
        print(f"Manifest not found: {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(MANIFEST_PATH, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if r.get("local_file") and r.get("wikipedia_title")]

    print(f"Manifest rows with image + wikipedia_title: {len(rows)}")
    cache = load_cache()
    print(f"Cached titles: {len(cache)}")

    ok = api_fail = 0
    for i, row in enumerate(rows, 1):
        title = row["wikipedia_title"]
        summary = fetch_summary(title, cache)
        if summary is None:
            api_fail += 1
            status = "no summary"
        else:
            record = shape_bio_record(row, summary)
            write_bio(record)
            ok += 1
            status = (summary.get("description") or "")[:60]

        if i <= 10 or i % 25 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] {title[:40]:<42} -> {status}")

        if i % 50 == 0:
            save_cache(cache)

    save_cache(cache)
    print(f"\nDone. wrote={ok}  api_fail={api_fail}  bios_dir={BIOS_DIR}")


if __name__ == "__main__":
    main()
