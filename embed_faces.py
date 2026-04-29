"""
Compute face embeddings for each portrait and write them in
Cloudflare Vectorize NDJSON format, ready for `wrangler vectorize insert`.

Reads:  wikimedia_portraits/manifest.csv (+ bios/{slug}.json for metadata)
Writes:
  wikimedia_portraits/embeddings.ndjson  — one Vectorize record per line
  wikimedia_portraits/embed_results.csv  — what happened per image

Embedding model: face_recognition's 128-D dlib face encoding (identity-trained,
not style/era — purpose-built for look-alike matching).

Detection: HOG by default (fast). For images where HOG misses (often paintings
or unusual lighting), retries with the CNN model.
"""

import csv
import hashlib
import json
import os
import sys
from pathlib import Path

import face_recognition
from dotenv import load_dotenv

ROOT = Path(__file__).parent
DATA = ROOT / "wikimedia_portraits"
MANIFEST = DATA / "manifest.csv"
BIOS_DIR = DATA / "bios"
OUTPUT = DATA / "embeddings.ndjson"
REPORT = DATA / "embed_results.csv"

METADATA_SUMMARY_CAP = 1500   # keeps full record under Vectorize's 10KB metadata limit
ID_MAX_LEN = 64               # Vectorize hard limit on vector IDs


def slug_from_image(image_path):
    return os.path.splitext(os.path.basename(image_path))[0]


def safe_id(slug, max_len=ID_MAX_LEN):
    """Vectorize caps vector IDs at 64 bytes. For longer slugs, keep a readable
    prefix and append an 8-char hash so the ID stays unique and recoverable."""
    if len(slug) <= max_len:
        return slug
    h = hashlib.md5(slug.encode()).hexdigest()[:8]
    return f"{slug[:max_len - 9]}_{h}"


def largest_face_index(face_locations):
    """face_locations: [(top, right, bottom, left), ...]  Return idx of biggest area."""
    if not face_locations:
        return None
    return max(range(len(face_locations)),
               key=lambda i: (face_locations[i][2] - face_locations[i][0])
                              * (face_locations[i][1] - face_locations[i][3]))


def load_bio(slug):
    path = BIOS_DIR / f"{slug}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def main():
    load_dotenv(ROOT / ".env")

    with open(MANIFEST, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("local_file")]

    print(f"Embedding {len(rows)} portraits with face_recognition (128-D)...")

    embeddings = []
    report = []
    embedded = no_face = multi_face = error = cnn_recovered = 0

    for i, row in enumerate(rows, 1):
        image_path = DATA / row["local_file"]
        slug = slug_from_image(row["local_file"])

        try:
            image = face_recognition.load_image_file(image_path)
        except Exception as exc:
            error += 1
            report.append({"slug": slug, "status": "load_error",
                           "info": str(exc)[:80], "face_count": 0})
            continue

        # Fast HOG pass first.
        face_locations = face_recognition.face_locations(image, model="hog")

        # Fallback to CNN for stylized/painted portraits HOG often misses.
        if not face_locations:
            face_locations = face_recognition.face_locations(
                image, model="cnn", number_of_times_to_upsample=1
            )
            if face_locations:
                cnn_recovered += 1

        n = len(face_locations)
        if n == 0:
            no_face += 1
            report.append({"slug": slug, "status": "no_face", "info": "",
                           "face_count": 0})
            continue
        if n > 1:
            multi_face += 1

        idx = largest_face_index(face_locations)
        encodings = face_recognition.face_encodings(image, [face_locations[idx]])
        if not encodings:
            error += 1
            report.append({"slug": slug, "status": "encode_failed",
                           "info": "", "face_count": n})
            continue

        vector = encodings[0].tolist()  # 128 floats

        bio = load_bio(slug)
        metadata = {
            "slug": slug,
            "name": bio.get("name") or row.get("extracted_name", ""),
            "description": bio.get("description", ""),
            "portrait_date": row.get("date", ""),
            "image_local": row["local_file"],
            "image_url": row.get("image_url", ""),
            "wikipedia_url": bio.get("wikipedia_url", ""),
            "summary": (bio.get("summary") or "")[:METADATA_SUMMARY_CAP],
        }

        embeddings.append({"id": safe_id(slug), "values": vector, "metadata": metadata})
        embedded += 1
        status = "embedded_multi" if n > 1 else "embedded"
        report.append({"slug": slug, "status": status, "info": "", "face_count": n})

        if i % 25 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] {slug[:48]}: ok ({n} face{'s' if n != 1 else ''})")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        for rec in embeddings:
            f.write(json.dumps(rec) + "\n")

    with open(REPORT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["slug", "status", "face_count", "info"])
        w.writeheader()
        w.writerows(report)

    print(f"\n=== Summary ===")
    print(f"  Embedded:           {embedded}  ({multi_face} had multiple faces; took largest)")
    print(f"  CNN-recovered:      {cnn_recovered}  (HOG missed, CNN found)")
    print(f"  No face detected:   {no_face}")
    print(f"  Errors:             {error}")
    print(f"  Output:             {OUTPUT}  ({OUTPUT.stat().st_size // 1024} KB)")
    print(f"  Per-row report:     {REPORT}")

    if no_face:
        no_face_slugs = [r["slug"] for r in report if r["status"] == "no_face"]
        print(f"\nNo face detected ({len(no_face_slugs)}):")
        for s in no_face_slugs[:15]:
            print(f"  - {s}")
        if len(no_face_slugs) > 15:
            print(f"  ...+ {len(no_face_slugs) - 15} more (see {REPORT.name})")


if __name__ == "__main__":
    main()
