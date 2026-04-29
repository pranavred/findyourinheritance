import { NextRequest, NextResponse } from "next/server";
import { spawn } from "child_process";
import { writeFile, unlink } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import { randomUUID } from "crypto";
import OpenAI from "openai";

export const runtime = "nodejs";
export const maxDuration = 60;

// project root = parent of /web
const PROJECT_ROOT = path.resolve(process.cwd(), "..");
const PYTHON = process.env.PYTHON_PATH
  ? path.resolve(process.cwd(), process.env.PYTHON_PATH)
  : path.join(PROJECT_ROOT, "venv/bin/python");
const EMBED_SCRIPT = path.join(PROJECT_ROOT, "embed_user.py");

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

  const systemPrompt = `You are a comedy writer voicing dead historical figures who have just discovered they are the user's long-lost ancestor — and they have a will to read out.

Write a single utterance in the figure's voice that:
1. Names them and claims a specific kinship (great-great-uncle, second cousin twice removed, the aunt nobody talks about, etc.)
2. Bequeaths something absurd, oddly specific, and ideally tied to a real detail from their bio — their work, scandals, era, obsessions, or signature failures.
3. Lands a small twist: an anachronism, a deflating mundane reason, a ridiculous condition, or a petty grievance.

STYLE: deadpan, slightly unhinged, period-flavored vocabulary, confidently weird. Under 50 words. No quotation marks. No em-dashes-as-pause-mechanism abuse.

DO NOT WRITE: "wisdom of the ages", "precious memories", "the legacy of", generic uplift, vague "treasures", "remember me when". If the line could be on a Hallmark card, rewrite it.

Examples of the right vibe:

Edgar Allan Poe (American writer, 1809–1849): I am Edgar Allan Poe, your great-great-uncle on the side of the family that never recovered. To you I leave 47 ravens (alive, demanding, judgmental), the unfinished couplet Quoth the —, and a single overdue library book from 1844. Settle the fine.

Marie Curie (Polish-French physicist, 1867–1934): I am your great-aunt Marie. You inherit two notebooks, three glass tubes, and a mildly radioactive cardigan I wore on Tuesdays. Wear it sparingly. The notebooks must be re-read every February. Don't ask why.

Sammy Davis Jr. (American singer and actor, 1925–1990): Cousin, I'm Sammy. You get my second-best tuxedo, half a martini I still owe somebody named Frank, and the moral obligation to tap-dance at exactly one wedding before you die. Anyone's wedding. Choose wisely.`;

  const userPrompt = [
    `Subject: ${name}`,
    description && `Description: ${description}`,
    portraitDate && `Portrait dated: ${portraitDate}`,
    `Bio: ${summary}`,
    ``,
    `Write their ancestor bequest line.`,
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
    max_tokens: 160,
  });

  return (
    completion.choices[0]?.message?.content?.trim() ||
    `${name} forgot what they were going to say.`
  );
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
