"""
HTTP face-embedding service.

Wraps the same `face_recognition` library used to embed the gallery, exposed
over HTTP so the Next.js API route on Vercel can call it without needing
Python in its own runtime.

Endpoints:
  GET  /              health check
  POST /embed         multipart {image: <file>} -> {vector: [128 floats], face_count}

Deploy targets supported by the same Dockerfile:
  - Cloudflare Containers (via wrangler deploy)
  - Render web service (Docker)
  - Fly.io (fly launch)
  - Hugging Face Spaces (Docker SDK)
"""

import io
import os

import face_recognition
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Face Embedding Service")

# Allow our Next.js frontend to call us. For tighter prod, replace * with the
# specific Vercel domain via an env var like ALLOWED_ORIGINS.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {"status": "ok", "service": "face-embed", "model": "face_recognition (dlib 128-D)"}


@app.post("/embed")
async def embed(image: UploadFile = File(...)):
    contents = await image.read()
    try:
        img = face_recognition.load_image_file(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"load_failed: {str(exc)[:120]}")

    locations = face_recognition.face_locations(img, model="hog")
    if not locations:
        locations = face_recognition.face_locations(
            img, model="cnn", number_of_times_to_upsample=1
        )

    if not locations:
        raise HTTPException(status_code=422, detail="no_face")

    face_count = len(locations)

    if face_count > 1:
        # Pick the largest face by bbox area.
        locations = [
            max(locations, key=lambda l: (l[2] - l[0]) * (l[1] - l[3]))
        ]

    encodings = face_recognition.face_encodings(img, locations)
    if not encodings:
        raise HTTPException(status_code=500, detail="encode_failed")

    return {
        "vector": encodings[0].tolist(),
        "face_count": face_count,
    }
