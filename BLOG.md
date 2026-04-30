# Find Your Inheritance — build notes

I built a thing in a day called Find Your Inheritance. Upload a photo, the site finds the historical figure you most resemble, then animates that portrait — actually animates the painting or the daguerreotype — and has them claim to be your great-uncle reading aloud some absurd inheritance they're leaving you. Edgar Allan Poe might bequeath you 47 demanding ravens. Marie Curie might leave you "a mildly radioactive cardigan I wore on Tuesdays" with strict instructions to wear it sparingly.

It works. Repo at [github.com/pranavred/findyourinheritance](https://github.com/pranavred/findyourinheritance).

I want to write down what it actually took, because most of the choices were not the obvious ones, and a couple I changed mid-build. Hackathon write-ups that go "I used Next.js, Vercel, and OpenAI — here it is" leave out the part where everything was on fire for an hour at 4pm. So:

## The dataset

First instinct: scrape the Met Museum's open API. They have a portrait tag and a great public collection. I pulled 72 images in five minutes.

It was useless. The Met "portrait" tag is generous. It includes calligraphy ("Poem of Farewell to Liu Man"), horses (Han Gan's famous *Night-Shining White* is a horse, not a man), unspecified cabinet cards, and a lot of stylized religious art. About a third of what came back wasn't even a face. About half had no named subject — meaning even if I could match a user's photo to "Portrait of a young woman, ca. 1830," the joke prompt would have nothing to riff on.

Pivoted to Wikimedia Commons' [Featured Pictures / Historical / People](https://commons.wikimedia.org/wiki/Commons:Featured_pictures/Historical/People). 419 entries, hand-curated for image quality by Wikipedia editors. Better signal-to-noise from line one.

Still: roughly a third of those entries are anonymous. "Unidentified woman, daguerreotype by Southworth & Hawes, ca. 1850." Beautiful image, useless to me — there's no biography to write a will from.

The filter that saved this part of the project: extract a candidate person name from the gallery caption, then hit Wikipedia's REST summary endpoint. If it returns `type: "standard"` (a real article, not a disambiguation page), keep the entry. If it 404s or returns `disambiguation`, drop.

```python
def wikipedia_lookup(name, cache):
    url = WIKI_SUMMARY_URL.format(quote(name.replace(" ", "_")))
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if r.status_code == 200 and r.json().get("type") == "standard":
        return r.json().get("title")
    return None
```

This is the most useful one-line filter in the whole project. Anonymous portraits drop. Caption junk fails to resolve. The remaining entries all have a real face, a Wikipedia article, and a bio summary I can pipe into GPT-4o for the joke. 419 → 247 in one pass. After hand-deleting four group photos that wouldn't face-match cleanly: 243.

The name-extraction part was less elegant. Captions like "Self-portrait of Nadar," "Cabinet Card of Sojourner Truth - Collection of the National Museum of African American History and Culture," and "Sgt. Samuel Smith, African American soldier in Union uniform with wife and two daughters" all need different stripping rules. I went through about fifteen iterations of the regex — abbreviated honorifics (`Sgt.`, `Gen.`, `Cpl.`), "Self-portrait of," dash-separated photographer credits, parentheticals mid-string, circa-date markers (`c. 1862`, `ca. 1849`). Boring but necessary.

## I almost shipped CLIP

Plan was: embed each portrait with OpenAI CLIP via Replicate, push the vectors to Cloudflare Vectorize, run the same model client-side at query time. Standard playbook for "find similar images."

Then I stopped to think for thirty seconds.

CLIP embeds the **whole image** by visual similarity. It encodes color palette, composition, era, paint texture, lighting. What it does not specifically encode: facial identity.

Run the pipeline anyway and you get a disaster. A user's modern color selfie consistently matches other modern color selfies. Sepia 19th-century portraits cluster with each other. CLIP would happily tell every user they look like Edgar Allan Poe — not because they look like Poe, but because both photos share tonal range with other 19th-century portraits in the dataset.

Switched to dlib's 128-dimensional face encoding via the [`face_recognition`](https://github.com/ageitgey/face_recognition) Python library. Identity-trained. Background-invariant. Lighting-tolerant. The exact thing I needed. Trade-off: dlib has C dependencies, Apple Silicon installs can be miserable.

The first attempt at install went badly. I `pip install`ed face_recognition into the global anaconda Python, which pulled in numpy 2.x, which broke matplotlib for a dozen other projects on the same machine. Standard mistake; I knew better. Made a venv, reinstalled, also pinned `setuptools<80` because face_recognition_models still uses the legacy `pkg_resources` API which was removed in setuptools 80+. Embedding all 243 images then takes about a minute on CPU. 99% success rate; two paintings outright fail face detection (one harlequin costume, one abstract nude — both kind of fair).

## Cloudflare Vectorize

Vector DB choice was easy. The rest of the stack was going to live on Cloudflare anyway, and Vectorize accepts any dimension and any metric out of the box. Free tier covers the entire scale of this project by orders of magnitude.

```bash
wrangler vectorize create historical-portraits --dimensions=128 --metric=cosine
wrangler vectorize insert historical-portraits --file=embeddings.ndjson
```

Two hours of pipeline work compressed into two CLI commands. That's the dream.

One quiet gotcha: Vectorize caps vector IDs at 64 bytes. My slugs were derived from Wikimedia source filenames and some were absurd. The longest was 120 characters: `Robert_Howlett__Isambard_Kingdom_Brunel_Standing_Before_the_Launching_Chains_of_the_Great_Eastern__The_Metropolitan_Muse`. Truncation alone would risk collisions, so I kept a readable prefix and appended an 8-char MD5 hash:

```python
def safe_id(slug, max_len=64):
    if len(slug) <= max_len:
        return slug
    h = hashlib.md5(slug.encode()).hexdigest()[:8]
    return f"{slug[:max_len - 9]}_{h}"
```

Then I went through every layer (image filenames on disk, bio JSON filenames, manifest CSV, Vectorize IDs) and made sure they all use the same canonical form. One identifier, used everywhere. Took twenty minutes to track down all the places it needed propagating. Well worth not having to chase ID-mismatch bugs later.

## The talking head pipeline

When `/api/match` gets a vector hit from Vectorize, it does four things to produce the final video:

1. **GPT-4o** writes a one-line bequest, ≤25 words, "spoken aloud in 8 seconds or less." Three few-shot examples in the prompt to anchor tone (Poe's couplet, Curie's radioactive cardigan, Sammy Davis Jr.'s tap-dance obligation). Strict word cap. The line must reference a real detail from the bio. Explicit anti-instructions: no "wisdom of the ages," no "precious memories" — the prompt literally forbids them.
2. **ElevenLabs** TTS converts the line to ~10s of mp3. Voice depends on the figure's gender (more on this in a minute).
3. **Replicate's `veed/fabric-1.0`** takes the matched portrait + the audio and produces a 480p mp4 of the painting/photograph speaking the line. About 3 minutes of compute per video.
4. The Next.js route returns the video URL. The page autoplays.

Nothing exotic, but that 3-minute render is the whole bottleneck. There's no avoiding it for diffusion-based lipsync — fabric-1.0 generates 100 frames and each one needs the full pass. I bumped `maxDuration` on the route to 300s and added a polling loop with a 280s safety cutoff. If it ever takes longer than that, something's wrong with Replicate, not my code.

For local dev, the route shells out to Python via `child_process.spawn`. It's the kind of code you'd never put in production, and that's fine — for a hackathon it works:

```ts
const proc = spawn(PYTHON, [EMBED_SCRIPT, imagePath]);
proc.stdout.on("data", (chunk) => { stdout += chunk; });
proc.on("close", () => {
  resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}"));
});
```

For deployment, there's a FastAPI service in `embed_service/` ready to drop into a Cloudflare Container or Render — same library, just over HTTPS. The route flips between subprocess and HTTP based on whether `EMBED_SERVICE_URL` is set. Hackathon means shipping the demo locally first.

## The voice gender bug

Subtle one. Initially I picked the ElevenLabs voice based on pronouns in the matched figure's bio summary — count "he/him/his" vs "she/her/hers," pick male/female accordingly. Cheap and felt clever.

Worked for ~95% of the gallery. Then I tested with a friend who matched Phyllis Diller. Got back Bill — a "wise, mature, balanced" old male voice — saying her line.

I checked the bio. Phyllis Diller's Wikipedia summary uses zero pronouns either way: "Phyllis Ada Diller was an American stand-up comedian, actress, author, musician and visual artist..." It just uses her name throughout. My function returned "unknown" (zero == zero), which fell through to the male default.

Same problem on Ray Strachey, Hubertine Auclert, Voltairine de Cleyre. Encyclopedic writing about prominent women often does this — leads with the full name, never reaches for pronouns.

Fix: add a separate OpenAI call. `gpt-4o-mini`, temperature 0, JSON-only output, one job — classify gender from the bio. Ran it concurrently with the joke call via `Promise.all`, so total wall-time stays at the slower call:

```ts
const [joke, gender] = await Promise.all([
  generateJoke(match),
  classifyGender(match),
]);
```

Costs about $0.0001 extra per match. The pronoun heuristic stays in code as a fallback if the OpenAI call fails. Belt and suspenders.

The lesson here is the kind that's only obvious in hindsight: when a heuristic works in 95% of cases, the 5% failures aren't noise — they're systematically wrong in a way the heuristic itself can't see. Pronoun counting felt frugal. It was wrong in a way that didn't crash anything, just confidently produced bad output. Those are the meanest bugs.

## The UI

Redesigned the front-end the night before the demo. Started in standard "white card with rounded shadow" Tailwind. Fine for a CRUD app, not for this. There's something almost sacrilegious about having Edgar Allan Poe lipsync your inheritance in a design that looks like a Calendly form.

Went with a Nothing-inspired aesthetic. Warm off-white background, hairline borders only (no shadows, no rounded cards), Space Grotesk for body, Space Mono ALL CAPS for labels and metadata, **Doto** — Google's dot-matrix display font — for the one moment of typographic flex per screen, which is the matched person's name set absurdly large when the result lands. Three layers of hierarchy, four levels of opacity, one red accent (a single blinking dot during processing). Information-dense, no decoration. The aesthetic instructions for that style explicitly forbid making everything "secondary" — you have to commit to one thing being primary, even if it feels excessive at first.

Layout went to a 1/3 + 2/3 grid. Upload form on the left, result on the right. Side-by-side, not stacked. Means the demo recording shows the upload, the processing state, and the talking-head video all on screen at the same time, no scroll required.

## What I'd do differently

Three things worth flagging.

**The anaconda + pip blunder.** Always venv first. I knew this and forgot, broke a working anaconda env, spent twenty minutes recovering. Standard self-inflicted wound.

**Committed a Cloudflare Account ID to the public repo.** Account IDs aren't credentials — Cloudflare treats them as semi-public, like a username. But I left a literal ID in `.env.example` instead of a placeholder. No real damage (the API token, the actual secret, was always gitignored), but bad hygiene. Fixed it later. The history still has it. Force-pushing to scrub it isn't worth the cost — it's not actionable on its own and force-pushes break clones.

**Pronoun-counting for gender.** Should have just used the LLM from the start. The whole "let's count he/she" felt frugal, and it was — until it wasn't. A few hundredths of a cent per request to have GPT-4o-mini just *answer the question* would have saved me the bug.

## What surprised me

The Wikipedia-article-existence filter is the single best line of code in this project. It cuts the dataset by 40% in one HTTP call per entry and improves the quality of what remains dramatically. I'm reusing this filter every time I scrape something with biographical metadata.

The other surprise: image-to-video lipsync via fabric-1.0 actually works on painted portraits, not just photographs. Edgar Allan Poe's daguerreotype animates fine. Hector Berlioz's pencil sketch works. Mongkut, the King of Siam in state robes, works. There's a stylization range that breaks (engravings, very loose impressionist work), but most of the gallery renders cleanly. Lipsync models have gotten quietly excellent in the last year — the demo wouldn't have been possible at this quality even six months ago.

## Try it

- Repo: [github.com/pranavred/findyourinheritance](https://github.com/pranavred/findyourinheritance)
- Stack: face_recognition · Cloudflare Vectorize · GPT-4o + gpt-4o-mini · ElevenLabs · Replicate `veed/fabric-1.0` · Next.js 16

Built at a Productbuildersclub event hosted at Fabrik. Big thanks to Jeff for hosting.

If you fork it and find someone who looks like Tolstoy, send me the video.
