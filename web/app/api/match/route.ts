import { NextRequest, NextResponse } from "next/server";
import { spawn } from "child_process";
import { readFile, writeFile, unlink } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import { randomUUID } from "crypto";
import OpenAI from "openai";
import Replicate from "replicate";

export const runtime = "nodejs";
export const maxDuration = 300; // veed/fabric-1.0 takes ~3 min for 480p

// project root = parent of /web
const PROJECT_ROOT = path.resolve(process.cwd(), "..");
const PYTHON = process.env.PYTHON_PATH
  ? path.resolve(process.cwd(), process.env.PYTHON_PATH)
  : path.join(PROJECT_ROOT, "venv/bin/python");
const EMBED_SCRIPT = path.join(PROJECT_ROOT, "embed_user.py");
const PORTRAITS_DIR = path.join(PROJECT_ROOT, "wikimedia_portraits");

const FABRIC_MODEL_VERSION =
  "739bbce4edc07b0b1bd055998983324fe9a8ea18d854b5979423c5d6f62e5b78";
const VIDEO_RESOLUTION = "480p";

// Default ElevenLabs voices — picked per matched figure's gender (inferred
// from the bio's pronouns). All three IDs are confirmed available on the
// connected ElevenLabs account.
const VOICE_MALE = "pqHfZKP75CvOlQylNhV4"; // Bill — wise, mature, balanced (old)
const VOICE_FEMALE = "XrExE9yKIg1WjnnlVkGX"; // Matilda — knowledgable, professional (middle-aged)

interface EmbedResult {
  vector?: number[];
  face_count?: number;
  error?: string;
}

function embedImage(imagePath: string): Promise<EmbedResult> {
  return new Promise((resolve) => {
    const proc = spawn(PYTHON, [EMBED_SCRIPT, imagePath]);
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk) => { stdout += chunk; });
    proc.stderr.on("data", (chunk) => { stderr += chunk; });
    proc.on("error", (err) =>
      resolve({ error: `subprocess spawn failed: ${err.message}` })
    );
    proc.on("close", () => {
      const last = stdout.trim().split("\n").pop() || "";
      try {
        resolve(JSON.parse(last));
      } catch {
        resolve({
          error: `subprocess returned non-JSON. stderr=${stderr.slice(0, 200)} stdout=${last.slice(0, 200)}`,
        });
      }
    });
  });
}

interface VectorizeMatch {
  id: string;
  score: number;
  metadata: Record<string, string>;
}

async function queryVectorize(vector: number[]): Promise<VectorizeMatch | null> {
  const accountId = process.env.CLOUDFLARE_ACCOUNT_ID;
  const token = process.env.CLOUDFLARE_API_TOKEN;
  const indexName = process.env.VECTORIZE_INDEX || "historical-portraits";

  if (!accountId || !token) {
    throw new Error(
      "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be set in .env.local"
    );
  }

  const url = `https://api.cloudflare.com/client/v4/accounts/${accountId}/vectorize/v2/indexes/${indexName}/query`;

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      vector,
      topK: 1,
      returnMetadata: "all",
      returnValues: false,
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(
      `Vectorize query failed (${res.status}): ${text.slice(0, 300)}`
    );
  }

  const data = await res.json();
  const matches = data?.result?.matches || [];
  return matches[0] || null;
}

async function generateJoke(match: VectorizeMatch): Promise<string> {
  const apiKey = process.env.OPENAI_API_KEY;
  const name = match.metadata.name || "your ancestor";

  if (!apiKey) {
    return `${name} would say something here, but the OpenAI key isn't set.`;
  }

  const description = match.metadata.description || "";
  const portraitDate = match.metadata.portrait_date || "";
  const summary = match.metadata.summary || description || name;

  const systemPrompt = `You are a comedy writer voicing dead historical figures claiming kinship with the user and reading their will aloud.

Write a single utterance, **spoken aloud in 8 seconds or less (target 22 words, hard max 25)**, that:
1. Names the figure and claims a specific kinship.
2. Bequeaths something absurd, oddly specific, and tied to a real detail from their bio (work, scandals, era, obsessions, signature failures).
3. Lands a small twist — anachronism, mundane deflation, ridiculous condition, or petty grievance.

STYLE: deadpan, slightly unhinged, no quotation marks, no Hallmark warmth. If it could be on a sympathy card, rewrite it. If it goes over 25 words, rewrite it.

Examples (note the brevity):

Edgar Allan Poe (writer, 1809–1849): I'm your great-uncle Edgar. You inherit 47 demanding ravens, one half-finished couplet, and an overdue library fine from 1844.

Marie Curie (physicist, 1867–1934): I'm your aunt Marie. You get two notebooks, three glass tubes, and a mildly radioactive cardigan. Wear it sparingly.

Sammy Davis Jr. (singer, 1925–1990): Cousin, I'm Sammy. You inherit my second-best tuxedo and the duty to tap-dance at exactly one wedding. Choose wisely.`;

  const userPrompt = [
    `Subject: ${name}`,
    description && `Description: ${description}`,
    portraitDate && `Portrait dated: ${portraitDate}`,
    `Bio: ${summary}`,
    ``,
    `Write their bequest line (≤25 words, ~8 seconds spoken).`,
  ]
    .filter(Boolean)
    .join("\n");

  const client = new OpenAI({ apiKey });
  const completion = await client.chat.completions.create({
    model: "gpt-4o",
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user", content: userPrompt },
    ],
    temperature: 1.05,
    max_tokens: 80,
  });

  return (
    completion.choices[0]?.message?.content?.trim() ||
    `${name} forgot what they were going to say.`
  );
}

type Gender = "male" | "female" | "unknown";

function inferGender(text: string): Gender {
  if (!text) return "unknown";
  const lower = text.toLowerCase();
  // Word-boundary pronoun count over the bio summary.
  const male = (lower.match(/\b(he|him|his|himself)\b/g) || []).length;
  const female = (lower.match(/\b(she|her|hers|herself)\b/g) || []).length;
  if (male > female) return "male";
  if (female > male) return "female";
  return "unknown";
}

function pickVoice(gender: Gender): string {
  // A global override always wins (handy for testing one specific voice).
  if (process.env.ELEVENLABS_VOICE_ID) return process.env.ELEVENLABS_VOICE_ID;
  if (gender === "female") {
    return process.env.ELEVENLABS_VOICE_ID_FEMALE || VOICE_FEMALE;
  }
  // male or unknown -> male default
  return process.env.ELEVENLABS_VOICE_ID_MALE || VOICE_MALE;
}

async function generateAudio(text: string, voiceId: string): Promise<Buffer> {
  const apiKey = process.env.ELEVENLABS_API_KEY;
  if (!apiKey) {
    throw new Error("ELEVENLABS_API_KEY not set in .env.local");
  }

  const res = await fetch(
    `https://api.elevenlabs.io/v1/text-to-speech/${voiceId}`,
    {
      method: "POST",
      headers: {
        "xi-api-key": apiKey,
        "Content-Type": "application/json",
        Accept: "audio/mpeg",
      },
      body: JSON.stringify({
        text,
        model_id: "eleven_turbo_v2_5",
        voice_settings: {
          stability: 0.5,
          similarity_boost: 0.7,
          style: 0.2,
        },
      }),
    }
  );

  if (!res.ok) {
    const errBody = await res.text();
    throw new Error(
      `ElevenLabs TTS failed (${res.status}): ${errBody.slice(0, 200)}`
    );
  }

  const arrayBuffer = await res.arrayBuffer();
  return Buffer.from(arrayBuffer);
}

async function generateVideo(
  imageDataUri: string,
  audioDataUri: string
): Promise<string> {
  const apiKey = process.env.REPLICATE_API_TOKEN;
  if (!apiKey) {
    throw new Error("REPLICATE_API_TOKEN not set in .env.local");
  }

  const replicate = new Replicate({ auth: apiKey });

  const prediction = await replicate.predictions.create({
    version: FABRIC_MODEL_VERSION,
    input: {
      image: imageDataUri,
      audio: audioDataUri,
      resolution: VIDEO_RESOLUTION,
    },
  });

  // Poll until the prediction settles.
  const startedAt = Date.now();
  const TIMEOUT_MS = 280_000;
  let result = prediction;
  while (result.status === "starting" || result.status === "processing") {
    if (Date.now() - startedAt > TIMEOUT_MS) {
      throw new Error("Replicate fabric-1.0 prediction timed out after 280s");
    }
    await new Promise((r) => setTimeout(r, 3000));
    result = await replicate.predictions.get(result.id);
  }

  if (result.status !== "succeeded") {
    throw new Error(
      `Replicate prediction failed (${result.status}): ${result.error || "unknown error"}`
    );
  }

  // fabric-1.0 returns the video URL as a string output.
  const out = result.output;
  if (typeof out === "string") return out;
  if (Array.isArray(out) && typeof out[0] === "string") return out[0];
  throw new Error("Replicate returned unexpected output shape");
}

async function imageToDataUri(absPath: string): Promise<string> {
  const buf = await readFile(absPath);
  const ext = path.extname(absPath).toLowerCase();
  const mime =
    ext === ".png"
      ? "image/png"
      : ext === ".webp"
      ? "image/webp"
      : "image/jpeg";
  return `data:${mime};base64,${buf.toString("base64")}`;
}

function audioBufferToDataUri(buffer: Buffer): string {
  return `data:audio/mpeg;base64,${buffer.toString("base64")}`;
}

export async function POST(req: NextRequest) {
  let tempPath: string | null = null;
  try {
    const formData = await req.formData();
    const file = formData.get("image") as File | null;
    if (!file) {
      return NextResponse.json(
        { error: "No image uploaded (expected field 'image')" },
        { status: 400 }
      );
    }

    const buffer = Buffer.from(await file.arrayBuffer());
    tempPath = path.join(tmpdir(), `ancestor-upload-${randomUUID()}.jpg`);
    await writeFile(tempPath, buffer);

    const embed = await embedImage(tempPath);
    if (!embed.vector) {
      return NextResponse.json(
        { error: embed.error || "embedding failed" },
        { status: 422 }
      );
    }

    const match = await queryVectorize(embed.vector);
    if (!match) {
      return NextResponse.json({ error: "No match found" }, { status: 404 });
    }

    const joke = await generateJoke(match);

    // ── Talking head pipeline ─────────────────────────────
    // 1. Infer gender from the bio's pronouns → pick male/female voice.
    // 2. ElevenLabs TTS for the joke line.
    // 3. Pass matched portrait + audio to Replicate veed/fabric-1.0 (480p).
    // 4. Return the resulting video URL.
    const gender = inferGender(
      match.metadata.summary || match.metadata.description || ""
    );
    const voiceId = pickVoice(gender);
    const audioBuffer = await generateAudio(joke, voiceId);

    const portraitAbsPath = path.join(
      PORTRAITS_DIR,
      match.metadata.image_local
    );
    const imageDataUri = await imageToDataUri(portraitAbsPath);
    const audioDataUri = audioBufferToDataUri(audioBuffer);

    const videoUrl = await generateVideo(imageDataUri, audioDataUri);

    return NextResponse.json({
      match: {
        slug: match.metadata.slug,
        name: match.metadata.name,
        description: match.metadata.description,
        portrait_date: match.metadata.portrait_date,
        summary: match.metadata.summary,
        wikipedia_url: match.metadata.wikipedia_url,
        score: match.score,
      },
      face_count: embed.face_count,
      joke,
      video_url: videoUrl,
      voice: { gender, voice_id: voiceId },
    });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("/api/match error:", message);
    return NextResponse.json({ error: message }, { status: 500 });
  } finally {
    if (tempPath) {
      await unlink(tempPath).catch(() => {});
    }
  }
}
