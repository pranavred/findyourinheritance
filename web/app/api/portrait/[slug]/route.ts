import { NextRequest, NextResponse } from "next/server";
import { readFile, stat } from "fs/promises";
import path from "path";

export const runtime = "nodejs";

const IMAGES_DIR = path.resolve(
  process.cwd(),
  "..",
  "wikimedia_portraits",
  "images"
);

const MIME: Record<string, string> = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".webp": "image/webp",
};

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ slug: string }> }
) {
  const { slug } = await params;

  // Path-traversal guard: only safe slug characters.
  if (!/^[A-Za-z0-9._-]+$/.test(slug) || slug.startsWith(".") || slug.includes("..")) {
    return new NextResponse("Bad slug", { status: 400 });
  }

  for (const ext of Object.keys(MIME)) {
    const candidate = path.join(IMAGES_DIR, `${slug}${ext}`);
    try {
      const s = await stat(candidate);
      if (s.isFile()) {
        const data = await readFile(candidate);
        return new NextResponse(new Uint8Array(data), {
          headers: {
            "Content-Type": MIME[ext],
            "Cache-Control": "public, max-age=86400",
          },
        });
      }
    } catch {
      continue;
    }
  }

  return new NextResponse("Not found", { status: 404 });
}
