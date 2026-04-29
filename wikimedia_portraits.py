"""
Pull historical-people portraits from Wikimedia Commons' Featured Pictures page,
filtered to entries that resolve to a real Wikipedia article (named historical
figures only — drops "Unidentified woman", "Portrait of a daguerreotypist", etc.).

Source: https://commons.wikimedia.org/wiki/Commons:Featured_pictures/Historical/People

Pipeline:
  1. Parse 419 gallery entries from the page.
  2. Extract a candidate person name from each caption.
  3. Check Wikipedia (REST summary API) — keep entries whose name resolves to
     a 'standard' article (skip 404s and disambiguation pages).
  4. Download images for the survivors.

Wikipedia lookups are cached to disk so reruns are cheap.
"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, quote

import requests

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "wikimedia_portraits"
WIKI_CACHE = OUT_DIR / ".wiki_cache.json"
PAGE_URL = "https://commons.wikimedia.org/wiki/Commons:Featured_pictures/Historical/People"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"

HEADERS = {
    "User-Agent": (
        "MetPortraitsResearch/1.0 "
        "(https://commons.wikimedia.org/wiki/Commons:Featured_pictures/Historical/People; "
        "personal research) python-requests/2.32"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://commons.wikimedia.org/wiki/Commons:Featured_pictures/Historical/People",
}
TIMEOUT = 30
TARGET_WIDTH = 800
IMAGE_DELAY_SEC = 1.5  # Wikimedia CDN 429s aggressively under bursts
WIKI_DELAY_SEC = 0.1
MAX_429_RETRIES = 4
INITIAL_429_BACKOFF_SEC = 8

ENTRY_RE = re.compile(
    r'<li class="gallerybox".*?'
    r'<a href="/wiki/(File:[^"]+)"[^>]*?title="([^"]*)"[^>]*?>'
    r'<img[^>]+?src="([^"]+)"'
    r'.*?<div class="gallerytext">(.*?)</div>',
    re.DOTALL,
)
DATE_RE = re.compile(r"<b>(.*?)</b>\s*(?:<br\s*/?>)?\s*(.*)", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

# Phrases that mark a caption as anonymous / non-individual.
ANONYMOUS_PREFIXES = (
    "unidentified", "unknown", "an unknown", "a young", "a man", "a woman",
    "a boy", "a girl", "a child", "an old", "anonymous",
)

# Honorifics / ranks / professions to strip from the start of a name.
RANK_PREFIX_RE = re.compile(
    r"^(?:admiral|general|colonel|captain|lieutenant|major|sergeant|corporal|private|"
    r"president|king|queen|prince|princess|emperor|empress|duke|duchess|"
    r"count|countess|baron|baroness|earl|lord|lady|sir|dame|saint|reverend|"
    r"trapper|inspector|detective|"
    r"composer|sculptor|painter|photographer|architect|author|writer|artist|musician|"
    r"poet|playwright|scientist|inventor|explorer|"
    r"adm\.|gen\.|col\.|capt\.|lt\.|maj\.|sgt\.|cpl\.|pvt\.|pres\.|"
    r"st\.|mr\.|mrs\.|ms\.|dr\.|rev\.|prof\.|the honorable|the rt\. hon\.)"
    r"\s+",
    re.IGNORECASE,
)

# Trailing pose / scene phrases — cut the name off before these.
POSE_CUT_RE = re.compile(
    r"\s+(?:standing|seated|sitting|holding|reading|writing|wearing|"
    r"posing|riding|on horseback|with his|with her|with the|"
    r"before|behind|next to|in his|in her|in the)\s+",
    re.IGNORECASE,
)

# "c. 1862" / "circa 1862" date markers that sometimes trail a name.
CIRCA_DATE_RE = re.compile(r"\s+(?:c\.|ca\.|circa)\s*\d{2,4}.*$", re.IGNORECASE)

# Catalog noise that often trails a name.
CATALOG_NOISE_RE = re.compile(
    r"\s*[-—]?\s*(?:carte de visite|daguerreotype|ambrotype|tintype|"
    r"cabinet card|LCCN\s*\d+|FSA-OWI|NARA|cropped|restored|squared off|"
    r"\d{4}s?|\d{4}\s*[-–]\s*\d{4})\b.*$",
    re.IGNORECASE,
)


def fetch_page():
    r = requests.get(PAGE_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def upgrade_thumb_url(thumb_url, width=TARGET_WIDTH):
    return re.sub(r"/(\d+)px-([^/]+)$", rf"/{width}px-\2", thumb_url)


def clean_text(s):
    return html.unescape(TAG_RE.sub("", s)).strip()


def parse_entries(html_text):
    entries = []
    for m in ENTRY_RE.finditer(html_text):
        file_page, _title, thumb_src, gallerytext = m.groups()
        date_match = DATE_RE.search(gallerytext)
        if date_match:
            date = clean_text(date_match.group(1))
            caption = clean_text(date_match.group(2))
        else:
            date = ""
            caption = clean_text(gallerytext)
        entries.append({
            "file_page": "https://commons.wikimedia.org/wiki/" + file_page,
            "filename": unquote(file_page[len("File:"):]),
            "date": date,
            "caption": caption,
            "image_url": upgrade_thumb_url(thumb_src),
        })
    return entries


def extract_person_name(caption):
    """Heuristically pull a person name out of a gallery caption.

    Returns None if the caption looks anonymous or generic.

    Examples:
      "Edgar Allan Poe, by Mathew Brady"        -> "Edgar Allan Poe"
      "Self-portrait of Nadar"                  -> "Nadar"
      "Admiral John Dahlgren"                   -> "John Dahlgren"
      "Sher Ali (1825-1879) Amir of Afghanistan" -> "Sher Ali"
      "Isambard Kingdom Brunel standing before…" -> "Isambard Kingdom Brunel"
      "Unidentified woman, daguerreotype..."    -> None
    """
    if not caption:
        return None
    s = caption.strip()

    # Strip parenthetical content anywhere in the string.
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s).strip()

    # Strip leading "Portrait of ", "Self-portrait of ", "Cabinet card of ", etc.
    s = re.sub(
        r"^(?:a\s+|an\s+)?(?:self[- ]portrait|photograph|portrait|painting|"
        r"miniature|daguerreotype|carte de visite|cabinet card|cabinet photo|"
        r"drawing|sketch|engraving|woodcut|lithograph)\s+of\s+",
        "", s, flags=re.IGNORECASE,
    ).strip()

    # Cut off photographer/artist credit.
    s = re.split(
        r"\s*,\s*by\s+|\s+by\s+|\s*,\s*photographed\s+by\s+|\s*,\s*painted\s+by\s+",
        s, maxsplit=1, flags=re.IGNORECASE,
    )[0]

    # Cut at pose phrases ("Brunel standing before..." -> "Brunel").
    s = POSE_CUT_RE.split(s, maxsplit=1)[0]

    # Cut everything after a comma (era/role descriptors we don't want in queries).
    s = s.split(",")[0].strip()

    # Strip "c. 1862" style date markers anywhere in/trailing the name.
    s = CIRCA_DATE_RE.sub("", s).strip()

    # Strip catalog noise trailing the name (LCCN, dates, formats).
    s = CATALOG_NOISE_RE.sub("", s).strip()

    # Strip a leading rank/honorific so "Admiral John Dahlgren" -> "John Dahlgren".
    s = RANK_PREFIX_RE.sub("", s).strip()

    # Cut at " of X" if "of" appears mid-name (handles "Sher Ali Amir of Afghanistan").
    # Only cut if there's still something meaningful before " of ".
    of_match = re.search(r"\s+of\s+", s, flags=re.IGNORECASE)
    if of_match and of_match.start() > 0:
        before = s[:of_match.start()].strip()
        if before and re.search(r"\b[A-Z][a-z]", before):
            s = before

    if not s:
        return None
    if s.lower().startswith(ANONYMOUS_PREFIXES):
        return None
    if not re.search(r"\b[A-Z][a-z]", s):
        return None
    return s


def load_wiki_cache():
    if WIKI_CACHE.exists():
        try:
            return json.loads(WIKI_CACHE.read_text())
        except Exception:
            return {}
    return {}


def save_wiki_cache(cache):
    WIKI_CACHE.parent.mkdir(parents=True, exist_ok=True)
    WIKI_CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))


def wikipedia_lookup(name, cache):
    """Return the canonical Wikipedia title if name resolves to a standard
    article, else None. Caches results."""
    if name in cache:
        return cache[name]

    url = WIKI_SUMMARY_URL.format(quote(name.replace(" ", "_")))
    try:
        r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]},
                         timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get("type") == "standard":
                result = data.get("title") or name
            else:
                # disambiguation page or other non-article — skip
                result = None
        else:
            result = None
    except Exception:
        result = None

    cache[name] = result
    time.sleep(WIKI_DELAY_SEC)
    return result


def filter_entries(entries, cache):
    """Annotate each entry with extracted_name & wikipedia_title; return only
    those that pass both filters."""
    kept, dropped_anon, dropped_nohit = [], [], []
    for i, e in enumerate(entries, 1):
        if i % 50 == 0:
            print(f"  filtering {i}/{len(entries)}...")
        name = extract_person_name(e["caption"])
        if not name:
            dropped_anon.append(e)
            continue
        wiki_title = wikipedia_lookup(name, cache)
        if not wiki_title:
            dropped_nohit.append({**e, "extracted_name": name})
            continue
        kept.append({**e, "extracted_name": name, "wikipedia_title": wiki_title})
    return kept, dropped_anon, dropped_nohit


SLUG_MAX_LEN = 64  # matches Cloudflare Vectorize ID limit so the same name flows everywhere


def slug_for(entry, idx):
    """Derive a filename slug from a Commons file entry.

    Caps at 64 chars (Vectorize's vector ID limit). For long names, keeps a
    readable prefix and appends an 8-char hash so the slug stays unique.
    """
    base = os.path.splitext(entry["filename"])[0]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")
    if not base:
        base = f"portrait_{idx:03d}"
    if len(base) <= SLUG_MAX_LEN:
        return base
    h = hashlib.md5(base.encode()).hexdigest()[:8]
    return f"{base[:SLUG_MAX_LEN - 9]}_{h}"


def download_image(entry, out_path):
    if out_path.exists():
        return True, "skipped (exists)"
    backoff = INITIAL_429_BACKOFF_SEC
    for attempt in range(MAX_429_RETRIES + 1):
        try:
            r = requests.get(entry["image_url"], headers=HEADERS,
                             timeout=TIMEOUT, stream=True)
        except Exception as e:
            return False, str(e)

        if r.status_code == 200:
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            return True, out_path.name

        if r.status_code == 429:
            if attempt == MAX_429_RETRIES:
                return False, f"http 429 (gave up after {attempt + 1} tries)"
            wait = int(r.headers.get("Retry-After", "0")) or backoff
            time.sleep(wait)
            backoff *= 2
            continue

        return False, f"http {r.status_code}"
    return False, "unreachable"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap number of images downloaded (after filtering)")
    ap.add_argument("--filter-only", action="store_true",
                    help="Run the parser + Wikipedia filter, print summary, don't download")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {PAGE_URL}")
    html_text = fetch_page()
    entries = parse_entries(html_text)
    print(f"Parsed {len(entries)} gallery entries.")

    print("Filtering against Wikipedia...")
    cache = load_wiki_cache()
    kept, dropped_anon, dropped_nohit = filter_entries(entries, cache)
    save_wiki_cache(cache)

    print(f"\n=== Filter summary ===")
    print(f"  Total parsed:                {len(entries)}")
    print(f"  Dropped (no person name):    {len(dropped_anon)}")
    print(f"  Dropped (no Wikipedia hit):  {len(dropped_nohit)}")
    print(f"  Kept (named historical):     {len(kept)}")

    print(f"\n  Sample KEPT (first 15):")
    for e in kept[:15]:
        print(f"    [{e['date']:<14}] {e['extracted_name']!r:<35} -> wiki: {e['wikipedia_title']}")

    print(f"\n  Sample DROPPED — no person name (first 8):")
    for e in dropped_anon[:8]:
        print(f"    [{e['date']:<14}] {e['caption']}")

    print(f"\n  Sample DROPPED — no Wikipedia hit (first 8):")
    for e in dropped_nohit[:8]:
        print(f"    [{e['date']:<14}] tried {e['extracted_name']!r} (caption: {e['caption']})")

    if args.filter_only:
        return

    targets = kept if args.limit is None else kept[:args.limit]
    images_dir = OUT_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "manifest.csv"

    print(f"\nDownloading {len(targets)} images to {images_dir}/")
    rows, ok, fail = [], 0, 0
    for i, e in enumerate(targets, 1):
        ext = os.path.splitext(e["filename"])[1].lower() or ".jpg"
        local = images_dir / f"{slug_for(e, i)}{ext}"
        if i > 1:
            time.sleep(IMAGE_DELAY_SEC)
        success, info = download_image(e, local)
        if success:
            ok += 1
        else:
            fail += 1
        if i <= 10 or i % 25 == 0 or i == len(targets):
            print(f"  [{i}/{len(targets)}] {e['extracted_name'][:50]!r:<52} -> {info}")
        rows.append({
            "local_file": local.relative_to(OUT_DIR).as_posix() if success else "",
            "extracted_name": e["extracted_name"],
            "wikipedia_title": e["wikipedia_title"],
            "date": e["date"],
            "original_caption": e["caption"],
            "image_url": e["image_url"],
            "file_page": e["file_page"],
            "download_status": "ok" if success else f"fail: {info}",
        })

    fields = ["local_file", "extracted_name", "wikipedia_title", "date",
              "original_caption", "image_url", "file_page", "download_status"]
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"\nDone. ok={ok} fail={fail}")
    print(f"Manifest: {manifest_path}")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
