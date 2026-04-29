"use client";

import { useState, useRef, useEffect } from "react";

interface MatchData {
  slug: string;
  name: string;
  description: string;
  portrait_date: string;
  summary: string;
  wikipedia_url: string;
  score: number;
}

interface MatchResponse {
  match: MatchData;
  joke: string;
  video_url?: string;
  face_count?: number;
}

const RECEIPT_STEPS: Array<[string, string, string]> = [
  ["01", "Embed the face", "face_recognition"],
  ["02", "Query Vectorize", "128-D · cosine"],
  ["03", "Draft the will", "gpt-4o"],
  ["04", "Record the voice", "elevenlabs"],
  ["05", "Animate the portrait", "fabric-1.0 · 480p"],
];

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<MatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [cameraOn, setCameraOn] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);

  function clearResult() {
    setResult(null);
    setError(null);
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0];
    if (!selected) return;
    setFile(selected);
    setPreview(URL.createObjectURL(selected));
    clearResult();
  }

  async function startCamera() {
    setCameraError(null);
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: 1280, height: 720 },
        audio: false,
      });
      setStream(s);
      setCameraOn(true);
      requestAnimationFrame(() => {
        if (videoRef.current) videoRef.current.srcObject = s;
      });
    } catch (e) {
      setCameraError(
        e instanceof Error ? e.message : "Camera permission denied."
      );
    }
  }

  function stopCamera() {
    stream?.getTracks().forEach((t) => t.stop());
    setStream(null);
    setCameraOn(false);
  }

  function capturePhoto() {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        const captured = new File([blob], "camera-capture.jpg", {
          type: "image/jpeg",
        });
        setFile(captured);
        setPreview(URL.createObjectURL(blob));
        stopCamera();
        clearResult();
      },
      "image/jpeg",
      0.92
    );
  }

  useEffect(() => {
    return () => {
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [stream]);

  async function handleSubmit() {
    if (!file) return;
    setLoading(true);
    clearResult();

    const formData = new FormData();
    formData.append("image", file);

    try {
      const res = await fetch("/api/match", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || `Server error ${res.status}`);
      } else {
        setResult(data);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setLoading(false);
    }
  }

  const verdictStatus = loading
    ? "PROCESSING"
    : result?.match
    ? "DELIVERED"
    : "AWAITING";

  return (
    <main className="min-h-screen bg-[var(--background)] text-[color:var(--text-primary)] flex flex-col">
      {/* ── HEADER ─────────────────────────────────────── */}
      <header className="px-6 md:px-10 lg:px-14 pt-8 md:pt-12">
        <div className="flex items-center justify-between gap-4">
          <span className="label">Find Your Inheritance / 2026</span>
          <span className="label">
            <span className="opacity-50">N=</span>241 ancestors indexed
          </span>
        </div>

        <div className="mt-4 border-t hairline" />

        <div className="mt-10 md:mt-14 grid grid-cols-1 md:grid-cols-3 gap-6 md:gap-12 items-end">
          <h1 className="md:col-span-2 text-5xl md:text-6xl lg:text-7xl font-light tracking-tight leading-[0.98]">
            Find Your Inheritance.
          </h1>
          <p className="text-base md:text-lg opacity-60 max-w-md leading-snug">
            Upload your face. We find the historical figure you most resemble
            — and they bequeath you something stupid, on camera, in their own
            voice.
          </p>
        </div>
      </header>

      {/* ── MAIN GRID ──────────────────────────────────── */}
      <section className="flex-1 px-6 md:px-10 lg:px-14 mt-16 md:mt-20">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-10 md:gap-14">
          {/* LEFT — UPLOAD ─────────────────────── */}
          <div className="md:col-span-1">
            <div className="flex items-baseline justify-between border-t hairline pt-3">
              <span className="label-strong">01 / Subject</span>
              <span className="label">{file ? "Ready" : "Empty"}</span>
            </div>

            <div className="mt-8 space-y-5">
              {!cameraOn && (
                <>
                  <label className="block cursor-pointer group">
                    <input
                      type="file"
                      accept="image/*"
                      onChange={handleFileChange}
                      className="sr-only"
                      disabled={loading}
                    />
                    <span className="block py-3 px-5 border hairline text-center font-mono uppercase text-xs tracking-[0.18em] group-hover:bg-stone-900 group-hover:text-[var(--background)] transition-colors">
                      + Upload image
                    </span>
                  </label>

                  <button
                    onClick={startCamera}
                    disabled={loading}
                    className="w-full py-3 px-5 border hairline font-mono uppercase text-xs tracking-[0.18em] hover:bg-stone-900 hover:text-[var(--background)] transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    ◉ Use camera
                  </button>

                  {cameraError && (
                    <p className="font-mono text-xs text-[var(--accent)] mt-2">
                      {cameraError}
                    </p>
                  )}

                  {preview && (
                    <div className="mt-6">
                      <span className="label">Preview</span>
                      <div className="mt-2 border hairline">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={preview}
                          alt="Your upload"
                          className="w-full max-h-72 object-contain bg-white"
                        />
                      </div>
                    </div>
                  )}
                </>
              )}

              {cameraOn && (
                <div className="space-y-3">
                  <span className="label">Live</span>
                  <video
                    ref={videoRef}
                    autoPlay
                    playsInline
                    muted
                    className="w-full bg-black scale-x-[-1] aspect-[4/3] object-cover border hairline"
                  />
                  <div className="grid grid-cols-2 gap-3">
                    <button
                      onClick={capturePhoto}
                      className="py-3 bg-stone-900 text-[var(--background)] font-mono uppercase text-xs tracking-[0.18em] hover:bg-stone-700 transition-colors"
                    >
                      ● Capture
                    </button>
                    <button
                      onClick={stopCamera}
                      className="py-3 border hairline font-mono uppercase text-xs tracking-[0.18em] hover:bg-stone-100 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              <button
                onClick={handleSubmit}
                disabled={!file || loading || cameraOn}
                className="w-full py-4 mt-4 bg-stone-900 text-[var(--background)] font-mono uppercase text-xs tracking-[0.18em] hover:bg-stone-700 transition-colors disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center gap-3"
              >
                {loading ? (
                  <>
                    <span className="blink-dot text-[var(--accent)]">●</span>
                    Processing…
                  </>
                ) : (
                  <>Claim inheritance →</>
                )}
              </button>

              {loading && (
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] opacity-50 leading-relaxed">
                  Embedding · Matching · Drafting · Recording · Animating.
                  ~2–3 min.
                </p>
              )}
            </div>
          </div>

          {/* RIGHT — RESULT ─────────────────────── */}
          <div className="md:col-span-2">
            <div className="flex items-baseline justify-between border-t hairline pt-3">
              <span className="label-strong">02 / Verdict</span>
              <span className="label flex items-center gap-2">
                {loading && (
                  <span className="blink-dot text-[var(--accent)]">●</span>
                )}
                {verdictStatus}
              </span>
            </div>

            {error && (
              <div className="mt-8 border-l-2 border-[var(--accent)] pl-4 py-1">
                <p className="label-strong text-[var(--accent)]">Error</p>
                <p className="mt-2 font-mono text-sm break-words">{error}</p>
              </div>
            )}

            {!error && result?.match && (
              <div className="mt-8">
                {/* The video — the hero artifact */}
                <div className="border hairline bg-black">
                  {result.video_url ? (
                    <video
                      src={result.video_url}
                      controls
                      autoPlay
                      playsInline
                      className="w-full max-h-[28rem] object-contain bg-black"
                    />
                  ) : (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={`/api/portrait/${result.match.slug}`}
                      alt={result.match.name}
                      className="w-full max-h-[28rem] object-contain bg-stone-50"
                    />
                  )}
                </div>

                {/* The matched name — the ONE display moment */}
                <div className="mt-10">
                  <span className="label">Your ancestor</span>
                  <h2 className="display-name mt-3 text-5xl md:text-6xl lg:text-7xl">
                    {result.match.name}
                  </h2>
                  {result.match.description && (
                    <p className="mt-3 text-base md:text-lg opacity-60 italic">
                      {result.match.description}
                    </p>
                  )}
                </div>

                {/* The bequest line */}
                <div className="mt-12 max-w-3xl">
                  <span className="label">The will</span>
                  <p className="mt-4 text-xl md:text-2xl lg:text-3xl leading-snug font-light">
                    {result.joke}
                  </p>
                </div>

                {/* Metadata grid */}
                <div className="mt-12 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-6 max-w-2xl">
                  <div className="border-t hairline pt-3">
                    <div className="label">Portrait dated</div>
                    <div className="mt-2 font-mono text-sm">
                      {result.match.portrait_date || "—"}
                    </div>
                  </div>
                  <div className="border-t hairline pt-3">
                    <div className="label">Match score</div>
                    <div className="mt-2 font-mono text-sm">
                      {(result.match.score * 100).toFixed(1)}%
                    </div>
                  </div>
                  <div className="border-t hairline pt-3">
                    <div className="label">Faces detected</div>
                    <div className="mt-2 font-mono text-sm">
                      {result.face_count ?? 1}
                    </div>
                  </div>
                </div>

                <div className="mt-12">
                  <a
                    href={result.match.wikipedia_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono text-xs uppercase tracking-[0.18em] opacity-60 hover:opacity-100 transition-opacity inline-flex items-center gap-2 border-b hairline pb-1"
                  >
                    Read on Wikipedia
                    <span aria-hidden>→</span>
                  </a>
                </div>
              </div>
            )}

            {!error && !result?.match && (
              <div className="mt-8 space-y-12">
                <div>
                  <p className="text-base md:text-lg opacity-60 max-w-md leading-snug">
                    The matched portrait appears here once processed, voiced
                    by a verifiably dead historical figure of similar facial
                    geometry.
                  </p>
                </div>

                <div className="max-w-md">
                  <span className="label">Receipt</span>
                  <ol className="mt-4">
                    {RECEIPT_STEPS.map(([n, what, how]) => (
                      <li
                        key={n}
                        className="flex items-baseline gap-4 border-t hairline py-3"
                      >
                        <span className="font-mono text-xs opacity-40 w-6">
                          {n}
                        </span>
                        <span className="flex-1 text-sm">{what}</span>
                        <span className="font-mono text-[10px] uppercase tracking-[0.16em] opacity-40">
                          {how}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ── FOOTER ─────────────────────────────────────── */}
      <footer className="px-6 md:px-10 lg:px-14 py-8 mt-20 md:mt-24 border-t hairline">
        <div className="flex flex-wrap gap-x-8 gap-y-3 justify-between items-baseline">
          <span className="label">
            Wikimedia · CC-BY-SA · GPT-4o · ElevenLabs · Vectorize · Fabric-1.0
          </span>
          <a
            href="https://github.com/pranavred/findyourinheritance"
            target="_blank"
            rel="noopener noreferrer"
            className="label hover:opacity-100 transition-opacity"
          >
            github.com/pranavred/findyourinheritance ↗
          </a>
        </div>
      </footer>
    </main>
  );
}
