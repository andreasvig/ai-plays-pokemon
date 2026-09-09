# Online leaderboard — plan (v1, video first)

> Decision 2026-09-08: Cloudflare R2 for the heavy files, GitHub Pages for the
> page. Minimal by design: no backend, no accounts, nothing running. Publishing
> is one command from the machine that ran the run.

> **Revision 2026-09-08 (evening), Andreas: "we don't want traces on the live
> page, just result and video."** The default publish now sends the result row,
> the summary without its per-turn list, and the video. Trace + screenshots are
> opt-in (`--with-trace`). References to `trace.json` and screenshots below
> describe that opt-in path.

## 1. What v1 is

A public page, the existing leaderboard in the existing SimpleView aesthetic,
listing published runs. Each row opens the existing run report, and a row with a
recording plays it inline. Nothing is uploaded unless `pokemon publish` is run
for that run.

Not in v1: other people submitting runs, comments, auth, a custom domain,
YouTube. All are additive later; none change the shape below.

## 2. Shape

```
laptop                              Cloudflare R2 (public bucket)
─────────────────────────           ───────────────────────────────────
pokemon publish <run_id>  ──mp4──▶  runs/<run_id>/recording.mp4
                          ──png──▶  runs/<run_id>/screenshots/turn_0001.png …
                          ──json─▶  gh-pages branch (GitHub Pages)
                                    ───────────────────────────────────
                                    index.html + assets/   (static build of the SPA)
                                    data/leaderboard.json  (one row per published run)
                                    data/runs/<run_id>/summary.json
                                    data/runs/<run_id>/trace.json  (screenshot refs → R2 URLs)
```

Heavy bytes go to R2 (free egress, 10 GB free, ~$0.015/GB/month after). Only
JSON and the SPA bundle live in the `gh-pages` branch, so the repo stays small
regardless of how many runs are published. The recorder already writes H.264
MP4, so the page uses a plain `<video>`; no transcoding, no player service.

## 2b. Data handling — there is no database

Two dumb stores, and the local run folder stays the source of truth; nothing
leaves the machine unless `publish` is run for that run.

**Locally.** A run is one folder `local/runs/<run_id>/` (config, `events.jsonl`,
`conversation/`, `ocr/`, `screenshots/`, `savepoints/`, `state.json`,
`terminal.log`, `recording.mp4` if recorded). The control center keeps a flat
index of `RunSummary` rows in `local/app/runs_index.json`, rebuilt by scanning
the folders. The report is `trace.json`, built from the event log.

| Data | Local source | Published to | Why there |
|---|---|---|---|
| Summary row | the run's `RunSummary` | `data/leaderboard.json` + `data/runs/<id>/summary.json` (gh-pages) | tiny, versioned, read by the page |
| Report trace | `trace.json` | `data/runs/<id>/trace.json` (gh-pages) | small JSON; makes the row inspectable |
| Screenshots | `screenshots/` | R2 `runs/<id>/screenshots/…` | binary, a few MB per run |
| Video | `recording.mp4` | R2 `runs/<id>/recording.mp4` | binary, 7–230 MB |

**Never published:** `events.jsonl`, `conversation/`, `ocr/`, `savepoints/`,
`state.json`, `terminal.log`. Nobody online needs them and they are where
provider payloads live.

**The "table".** `leaderboard.json` is a list of rows keyed by `run_id`
(`RunSummary` fields + `video_url`, `screenshots_base_url`, `published_at`).
`publish` upserts the row, `unpublish` removes it. The page loads that one file
and sorts/filters client-side, exactly as the local leaderboard does over
`/api/leaderboard`. The trace is fetched only when a row is opened. Screenshot
references inside the trace are rewritten to R2 URLs at publish time, so the
trace is small and self-contained. Git history of `gh-pages` is the audit trail.

**Why it holds.** One writer (this laptop), so no locking. A row is well under
1 KB; ten thousand runs is still a small file. Deleting a run's media is an R2
prefix delete.

**Where it stops.** When other people should submit runs, the writer can no
longer be a git checkout: `leaderboard.json` becomes the seed of a small hosted
table (Supabase or similar) and the page reads that instead. The file layout,
the R2 keys and the static SPA do not change.

## 3. Pieces

### 3.1 `pokemon publish <run_id>` (new CLI command, `src/cli/publish.py`)

1. **Refuse unless the run is finished** and has a `run_summary` entry.
2. **Leak audit on the exact files about to leave the machine**: the summary,
   trace and config are grepped for every value in `.env`, for key shapes
   (`sk-…`, `AKIA…`, `Bearer …`), and for `/Users/` and `/private/tmp/` paths.
   Any hit aborts with the file and line. This is the audit run by hand on
   2026-09-08, made mechanical.
3. **Upload to R2** with boto3 against the S3 endpoint: `recording.mp4` (if
   present, or `--no-video`), and the run's screenshots. Keys as in §2.
   Content-Type set; idempotent (re-publish overwrites).
4. **Rewrite the trace** so screenshot references point at the R2 URLs, and
   drop anything the public report does not render (conversation dumps).
   `--no-trace` publishes the summary and video only.
5. **Update the `gh-pages` branch** through a git worktree under `local/`:
   write `data/runs/<run_id>/{summary,trace}.json`, upsert the row in
   `data/leaderboard.json` (row = `RunSummary` + `video_url`,
   `published_at`), rebuild the SPA in static mode into the worktree root,
   commit `publish <run_id>`, push.
6. **Verify the upload** by fetching the video URL anonymously — with a NAMED
   `User-Agent`. Cloudflare returns 403 to Python's default `Python-urllib`
   agent on `r2.dev` (observed 2026-09-08; curl, browsers and a named agent
   get 200), so a default-agent probe would report a false failure.
7. Print the page URL and the video URL.

`pokemon unpublish <run_id>` removes the row, the JSON and the R2 objects.

### 3.2 Static mode for the SPA (`src/dashboard/web`)

- A build flag (`VITE_STATIC=1`) makes `lib/api.js` read
  `data/leaderboard.json`, `data/runs/<id>/summary.json` and
  `data/runs/<id>/trace.json` instead of `/api/*`, and makes every other fetch
  resolve to an empty result.
- In static mode the app shows Leaderboard and Report only: no queue bar, no
  spectate, no history, no add-run dialog. The About page stays.
- Report: a video block above the turn rows when `summary.video_url` is set
  (`<video controls preload="metadata">`). Rows without a video show nothing.
- Base path: Pages serves at `/<repo>/`, so `vite build --base=/ai-plays-pokemon/`
  (or `/` if a custom domain is added later).

### 3.3 Configuration

`.env` (never committed; `.env.example` gains the names):

```
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=ai-plays-pokemon
R2_PUBLIC_BASE_URL=https://pub-<hash>.r2.dev     # or a custom subdomain later
```

Filled in on 2026-09-08 (`.env.example` documents where each comes from).
Access check passed the same day: list/put/head/delete through the S3 endpoint
with the bucket-scoped Object Read & Write token; anonymous GET over the public
URL returns 200 with the right Content-Type (see §3.1 step 6 for the UA trap).
The bucket is empty.

GitHub: Pages enabled on the `gh-pages` branch, root folder — possible only
after the first publish creates the branch.

## 4. What Andreas sets up once (cannot be done from here)

1. ✅ Cloudflare → R2 → bucket `ai-plays-pokemon` → Settings → Public
   Development URL enabled (`R2_PUBLIC_BASE_URL`). Note: the S3 endpoint
   `<account>.r2.cloudflarestorage.com` is NOT the public URL; it only answers
   signed requests (first attempt pasted that one; 400 anonymously).
2. ✅ R2 → Manage API tokens → Object Read & Write on that bucket → account id,
   access key id and secret in `.env`.
3. ✅ GitHub Pages — enabled itself when the first publish pushed a `gh-pages`
   branch (GitHub auto-enables Pages for that branch name; verified via
   `gh api repos/andreasvig/ai-plays-pokemon/pages` → status `built`, source
   `gh-pages` / root). Live at https://andreasvig.github.io/ai-plays-pokemon/.

## 5. Verification (live, not fixtures)

- Publish one finished run with a recording. Open the Pages URL with the
  browser skill: the row exists, the report opens, the video element has a
  `src` on the R2 host and reaches `readyState ≥ 1`, screenshots in the turn
  rows load from R2 (no 404s in the network log).
- Publish a run without a recording: row present, no video block.
- Re-publish the same run: one row, not two.
- Leak-audit control: plant a fake `sk-or-v1-…` string in a scratch copy of a
  summary and confirm publish refuses.
- `unpublish` removes the row and the objects.

## 6. Assumptions taken (say if wrong)

- A run without a recording can still be published; the video is optional.
- The default `r2.dev` URL is fine to start; a custom subdomain is a later,
  purely cosmetic step.
- Traces are public. The configs, including the 5.1 prompt, are already in
  the public repo, so a trace exposes the model's reasoning and nothing else.
- Screenshots go to R2 with the video, not into the Pages branch, so the repo
  does not grow ~4 MB per run.

## 7. Cost

R2 free tier: 10 GB storage, 10 M reads/month, egress free. Recordings run
7–230 MB, so ~40–50 long runs fit for free; past that ~$0.015/GB/month.
GitHub Pages: free for a public repo, 1 GB site soft limit (JSON only, fine).

## 8. Build order (after /compact, 2026-09-08) — DONE 2026-09-08

Built and verified the same day. Deviations from the text above, all additive:

- **History stays on the static site** (read-only: no continue / delete). §3.2
  said "Leaderboard and Report only", but the board is partitioned to official
  config-5.x runs and none exists yet, so a published casual run would have been
  unreachable. History is the list of everything published.
  **Revised 2026-09-09:** `publish` refuses anything but official
  completed/terminated runs, so the public History drops the kind badge, the
  status column and both filters; the hero says FireRed and loses the ranking
  rule note.
- **Trace size.** A published trace is 0.5 MB (12 turns) to 3.6 MB (94 turns)
  and 8.7 MB for the 170-turn gpt-5.6 run — bigger than "small JSON". Fine for
  tens of runs (gzip ≈ 10×); if the branch grows past a few hundred MB, move
  `trace.json` to R2 next to the screenshots (`trace_url` on the row, one fetch
  in `static.js`) — the layout otherwise holds.
- `data/benchmarks.json` is also published (the board's benchmark tabs).
- `--dry-run` and `--no-build` flags; `PAGES_BASE_URL` override for a custom domain.
- The static build's switch is a compile-time `__STATIC__` define (vite.config.js)
  rather than a runtime `import.meta.env` read, so the Pages bundle drops the
  control-center code (206 KB vs 220 KB) and node can import `lib/static.js`
  for tests.

Verification run (§5), 2026-09-08: two runs published (one recorded, one not),
19/19 browser assertions on the local Pages mimic AND on the live Pages URL,
unpublish → R2 prefix empty + video 404 + row gone, re-publish → one row.
Tests: `tests/test_publish.py` (20, incl. the planted-secret control) and
`tests/js/static.test.mjs` (5).


1. `src/cli/publish.py`: R2 client from `.env`, leak audit, upload, trace
   rewrite, gh-pages worktree write, verify-with-named-UA. Tests with a fake
   S3 client and a temp git repo; the leak audit gets a planted-secret control.
2. Static mode in the SPA: `VITE_STATIC` data adapter in `lib/api.js`,
   leaderboard + report only, video block in Report, Pages base path.
3. First real publish of a finished recorded run; enable Pages; browser-skill
   verification per §5. Then `unpublish` and re-publish to prove idempotence.

## 9. Later, not now

Custom domain; a YouTube cross-post from the publish command; `submit` for
other people's runs (needs a backend, Supabase would be the step); a nightly
Action that rebuilds the page from `data/` so the SPA build does not have to
happen on the laptop.
