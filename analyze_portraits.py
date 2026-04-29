"""
Analyze portraits_manifest.csv to estimate how many entries are actually
images of a person's face vs. other things the broad search queries pulled in
(landscapes, calligraphy, animal paintings, busts of objects, etc.).

The CSV only carries metadata, so we can't *prove* a row is a face — we'd need
to run face detection on the image. But several metadata signals get us close:
  - Title keywords ("portrait of", "self-portrait", "head of", "bust of", ...)
  - Title anti-keywords (animal names, scenes, calligraphy hints)
  - Classification (Calligraphy is almost never a face; Sculpture often is)
"""

import csv
import re
from collections import Counter
from pathlib import Path

CSV_PATH = Path(__file__).parent / "met_portraits" / "portraits_manifest.csv"

# Title patterns that strongly suggest a human face/figure portrait.
PORTRAIT_PATTERNS = [
    r"\bportrait\b",
    r"\bself[- ]portrait\b",
    r"\bhead of\b",
    r"\bbust of\b",
    r"\bface of\b",
    r"\blikeness of\b",
    r"\beffigy of\b",
    r"\bminiature of\b",
    r"\bportraiture\b",
]

# Anti-patterns: titles that suggest the subject is NOT a human face.
# Conservative — only flag clear non-face cues.
NON_FACE_PATTERNS = [
    r"\b(horse|horses|dog|cat|tiger|lion|bird|eagle|elephant|monkey|deer|ox|cow)\b",
    r"\b(landscape|mountain|river|garden|forest|seascape|harbor)\b",
    r"\b(flower|flowers|fruit|vase|still life)\b",
    r"\b(map|architecture|temple|building|palace|pavilion)\b",
    r"\b(poem|farewell|calligraphy|inscription|letter)\b",
    r"\b(battle|procession|hunt|festival|scene)\b",
]

PORTRAIT_RE = re.compile("|".join(PORTRAIT_PATTERNS), re.IGNORECASE)
NON_FACE_RE = re.compile("|".join(NON_FACE_PATTERNS), re.IGNORECASE)


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def classify(row):
    """Return one of: 'portrait', 'non_face', 'ambiguous'."""
    title = row["title"] or ""
    classification = (row["classification"] or "").lower()

    # Calligraphy is essentially never a face portrait.
    if "calligraphy" in classification:
        return "non_face"

    has_portrait_kw = bool(PORTRAIT_RE.search(title))
    has_non_face_kw = bool(NON_FACE_RE.search(title))

    if has_portrait_kw and not has_non_face_kw:
        return "portrait"
    if has_non_face_kw and not has_portrait_kw:
        return "non_face"
    if has_portrait_kw and has_non_face_kw:
        # e.g. "Portrait of a man with his horse" — keep as ambiguous
        return "ambiguous"
    return "ambiguous"


def main():
    rows = load_rows(CSV_PATH)
    print(f"Total rows: {len(rows)}\n")

    # Metadata distributions — show what filters are even available.
    print("=== Classification distribution ===")
    for cls, n in Counter(r["classification"] for r in rows).most_common():
        print(f"  {n:>3}  {cls or '(blank)'}")

    print("\n=== Department distribution ===")
    for dept, n in Counter(r["department"] for r in rows).most_common():
        print(f"  {n:>3}  {dept or '(blank)'}")

    # Title-based heuristic.
    buckets = {"portrait": [], "non_face": [], "ambiguous": []}
    for r in rows:
        buckets[classify(r)].append(r)

    print("\n=== Title-based heuristic verdict ===")
    for k in ("portrait", "non_face", "ambiguous"):
        print(f"  {k:>10}: {len(buckets[k]):>3}")

    print("\n=== Likely portraits (matched title keywords) ===")
    for r in buckets["portrait"]:
        print(f"  {r['objectID']:>7}  [{r['classification']:<14}]  {r['title']}")

    print("\n=== Likely NOT face portraits ===")
    for r in buckets["non_face"]:
        print(f"  {r['objectID']:>7}  [{r['classification']:<14}]  {r['title']}")

    print("\n=== Ambiguous (no decisive keyword either way) ===")
    for r in buckets["ambiguous"]:
        print(f"  {r['objectID']:>7}  [{r['classification']:<14}]  {r['title']}")


if __name__ == "__main__":
    main()
