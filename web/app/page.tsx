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
  face_count?: number;
}

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<MatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Camera state
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
      // Defer to next frame so the <video> element exists.
      requestAnimationFrame(() => {
        if (videoRef.current) {
          videoRef.current.srcObject = s;
        }
      });
    } catch (e) {
      setCameraError(
        e instanceof Error
          ? e.message
          : "Couldn't access the camera. Permission denied?"
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

  // Stop the camera if the component unmounts mid-stream.
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

  return (
    <main className="min-h-screen bg-gradient-to-br from-amber-50 via-stone-50 to-stone-100 p-6 md:p-10">
      <div className="max-w-3xl mx-auto">
        <header className="mb-8">
          <h1 className="text-4xl md:text-5xl font-serif text-stone-800 tracking-tight">
            Find Your Historical Ancestor
          </h1>
          <p className="mt-2 text-stone-600">
            Upload or snap a photo. We&apos;ll find the historical figure you
            most resemble — and they&apos;ll bequeath you something stupid.
          </p>
        </header>

        <section className="bg-white rounded-lg shadow-sm border border-stone-200 p-6 mb-6">
          {!cameraOn && (
            <>
              <div className="flex flex-col sm:flex-row gap-3">
                <label className="flex-1">
                  <span className="block text-sm font-medium text-stone-700 mb-2">
                    Upload from device
                  </span>
                  <input
                    type="file"
                    accept="image/*"
                    onChange={handleFileChange}
                    className="block w-full text-sm text-stone-600 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:bg-stone-800 file:text-white hover:file:bg-stone-700 file:cursor-pointer cursor-pointer"
                  />
                </label>
                <div className="flex flex-col">
                  <span className="block text-sm font-medium text-stone-700 mb-2">
                    Or use your camera
                  </span>
                  <button
                    onClick={startCamera}
                    className="px-4 py-2 bg-stone-100 hover:bg-stone-200 border border-stone-300 rounded-md text-sm text-stone-800 transition-colors"
                  >
                    📷 Take a photo
                  </button>
                </div>
              </div>

              {cameraError && (
                <p className="mt-3 text-sm text-red-700">{cameraError}</p>
              )}

              {preview && (
                <div className="mt-4">
                  <img
                    src={preview}
                    alt="Your upload"
                    className="max-h-64 rounded-md shadow-sm object-contain"
                  />
                </div>
              )}

              <button
                onClick={handleSubmit}
                disabled={!file || loading}
                className="mt-6 px-6 py-2 bg-stone-800 text-white rounded-md hover:bg-stone-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? "Searching the ages…" : "Find my ancestor"}
              </button>
            </>
          )}

          {cameraOn && (
            <div className="space-y-4">
              <p className="text-sm text-stone-600">
                Look into the camera. Click capture when you&apos;re ready.
              </p>
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className="w-full rounded-md bg-black scale-x-[-1] max-h-96 object-contain"
              />
              <div className="flex gap-3">
                <button
                  onClick={capturePhoto}
                  className="px-6 py-2 bg-stone-800 text-white rounded-md hover:bg-stone-700 transition-colors"
                >
                  📸 Capture
                </button>
                <button
                  onClick={stopCamera}
                  className="px-4 py-2 border border-stone-300 rounded-md text-stone-700 hover:bg-stone-50 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </section>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded-md mb-6">
            <strong className="font-medium">Couldn&apos;t process that:</strong>{" "}
            {error}
          </div>
        )}

        {result?.match && (
          <section className="bg-white rounded-lg shadow-sm border border-stone-200 p-6">
            <div className="flex flex-col md:flex-row gap-6">
              <img
                src={`/api/portrait/${result.match.slug}`}
                alt={result.match.name}
                className="w-full md:w-1/2 rounded-md shadow-sm object-contain max-h-96 bg-stone-50"
              />
              <div className="flex-1 min-w-0">
                <h2 className="text-2xl font-serif text-stone-800">
                  {result.match.name}
                </h2>
                {result.match.description && (
                  <p className="text-stone-600 italic mt-1">
                    {result.match.description}
                  </p>
                )}
                {result.match.portrait_date && (
                  <p className="text-stone-500 text-sm mt-1">
                    Portrait: {result.match.portrait_date}
                  </p>
                )}
                <p className="text-stone-500 text-sm mt-1">
                  Match similarity: {(result.match.score * 100).toFixed(1)}%
                </p>

                <blockquote className="mt-5 p-4 bg-amber-50 border-l-4 border-amber-400 italic text-stone-700">
                  {result.joke}
                </blockquote>

                <a
                  href={result.match.wikipedia_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-4 inline-block text-sm text-stone-600 hover:text-stone-800 underline"
                >
                  Read about {result.match.name} on Wikipedia →
                </a>
              </div>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
