# Face Embedding Service

A tiny FastAPI service that wraps `face_recognition` (dlib 128-D encodings) over HTTP. The Next.js `/api/match` route calls this when `EMBED_SERVICE_URL` is set; it falls back to a local Python subprocess when unset (for `npm run dev`).

It exists because Vercel and most JS-hosting platforms don't run Python with native deps like dlib — so the embedder lives here, the rest of the app lives on Vercel, and they talk over HTTPS.

## Endpoints

```
GET  /          → {"status": "ok", ...}
POST /embed     → multipart {image: <file>}
                  200 → {"vector": [128 floats], "face_count": N}
                  400 → {"detail": "load_failed: ..."}
                  422 → {"detail": "no_face"}
                  500 → {"detail": "encode_failed"}
```

## Local test

```bash
docker build -t face-embed .
docker run --rm -p 8080:8080 face-embed

# in another terminal
curl -F image=@../wikimedia_portraits/images/Edgar_Allan_Poe__circa_1849__restored__squared_off.jpg \
     http://localhost:8080/embed | head -c 200
```

## Deploy options

The same `Dockerfile` works for any of these. Pick one based on cost vs. cold-start tolerance.

### Render (free)

1. Push this repo to GitHub.
2. In the Render dashboard → New → Web Service → connect the repo.
3. Set **Root Directory** to `embed_service/`.
4. Render auto-detects the Dockerfile.
5. Plan: Free.
6. Deploy. The service URL shows in the dashboard, e.g. `https://face-embed-xxxx.onrender.com`.

> Free tier sleeps after 15 min idle, so the first request after a long pause takes ~30s. Subsequent requests are fast.

Then in `web/.env.local` (and Vercel env vars) set:
```
EMBED_SERVICE_URL=https://face-embed-xxxx.onrender.com
```

### Cloudflare Containers ($5/mo Workers Paid plan)

Cloudflare Containers attach to a small Worker entrypoint. The fastest path:

```bash
cd embed_service
npm create cloudflare@latest -- --template=cloudflare/templates/containers-template
# Replace the generated Dockerfile/main with the ones in this directory,
# point the worker entrypoint at our FastAPI container, then:
wrangler deploy
```

After deploy, your Worker URL is the `EMBED_SERVICE_URL`. No cold starts. The Worker can also be wired to call Vectorize directly via binding, eliminating the REST API hop — but that's an optional optimization for later.

### Fly.io

```bash
cd embed_service
flyctl launch        # accept defaults; it auto-detects Dockerfile
flyctl deploy
```

### Hugging Face Spaces

1. Create a new Space → Docker SDK.
2. Upload `main.py`, `requirements.txt`, `Dockerfile`.
3. Wait for build, copy the URL.

## Notes

- The first build is slow (~3–5 min) because dlib compiles from source.
- Docker layer caching means subsequent code changes redeploy in ~30s.
- All platforms above respect the `PORT` env var the Dockerfile uses.
