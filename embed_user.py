"""
Embed a user-uploaded face image to a 128-D face_recognition vector.

Used by the Next.js /api/match route as a subprocess. Prints a single JSON
object to stdout so the caller can parse it directly.

Usage:
  venv/bin/python embed_user.py <image_path>

Stdout (always one JSON line):
  {"vector": [128 floats], "face_count": N}
  {"error": "no_face"}
  {"error": "load_failed: <details>"}
"""

import json
import sys

import face_recognition


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: embed_user.py <image_path>"}))
        sys.exit(1)

    image_path = sys.argv[1]

    try:
        image = face_recognition.load_image_file(image_path)
    except Exception as exc:
        print(json.dumps({"error": f"load_failed: {str(exc)[:100]}"}))
        sys.exit(0)

    # Fast HOG first, slow CNN as fallback for hard cases.
    locations = face_recognition.face_locations(image, model="hog")
    if not locations:
        locations = face_recognition.face_locations(
            image, model="cnn", number_of_times_to_upsample=1
        )

    if not locations:
        print(json.dumps({"error": "no_face"}))
        sys.exit(0)

    face_count = len(locations)

    # Pick the largest face if multiple were detected.
    if face_count > 1:
        locations = [max(locations,
                         key=lambda l: (l[2] - l[0]) * (l[1] - l[3]))]

    encodings = face_recognition.face_encodings(image, locations)
    if not encodings:
        print(json.dumps({"error": "encode_failed"}))
        sys.exit(0)

    print(json.dumps({
        "vector": encodings[0].tolist(),
        "face_count": face_count,
    }))


if __name__ == "__main__":
    main()
