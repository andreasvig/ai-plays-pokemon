// Static mode — the published site on GitHub Pages (artifacts/online-leaderboard/plan.md).
//
// Built with `VITE_STATIC=1 vite build --base=/<repo>/` by `pokemon publish`. There
// is no server behind the page: `api.js` routes every read through `staticGet`
// below, which answers from the JSON files the publisher commits to the
// `gh-pages` branch, and every write / socket becomes a no-op. The components
// import `STATIC` to hide what cannot work without a control center (queue,
// spectate, continue, delete, mute).
//
//   data/leaderboard.json          one row per published run (RunSummary + video_url, …)
//   data/benchmarks.json           the registry, for the board's benchmark tabs
//   data/runs/<run_id>/summary.json  (run_summary.json minus its per-turn list)
//   data/runs/<run_id>/trace.json    only when published with --with-trace; the
//                                    default is result + video, and Report renders
//                                    no turn section when this 404s
//
// In a normal build `VITE_STATIC` is unset and every export here is inert.

// `__STATIC__` is defined by vite.config.js from VITE_STATIC (a compile-time
// constant, so the bundler can drop the live-app code paths); plain node
// (tests/js) has neither it nor `import.meta.env`, so both are read guarded.
/* global __STATIC__ */
export const STATIC = typeof __STATIC__ !== 'undefined' ? __STATIC__ : false
const ENV = typeof import.meta.env === 'undefined' ? {} : import.meta.env

/** Vite's base path: '/' locally, '/ai-plays-pokemon/' on Pages. Always ends in '/'. */
export const BASE = (ENV.BASE_URL || '/').replace(/\/?$/, '/')

/** Best official run per model, farthest then fastest — `derivations.leaderboard`
 *  in JS, so the static board ranks exactly as the local one does. */
export const LEADERBOARD_CONFIG_PREFIX = 'config-5.'

export function eligible(row) {
  return row.kind === 'official'
    && (row.status === 'completed' || row.status === 'terminated')
    && typeof row.config_stem === 'string' && row.config_stem.startsWith(LEADERBOARD_CONFIG_PREFIX)
}

export function rankBoard(rows, benchmark = null) {
  const best = new Map()
  for (const r of rows) {
    if (!eligible(r)) continue
    if (benchmark != null && r.benchmark !== benchmark) continue
    const cur = best.get(r.model)
    const better = !cur
      || (r.gates_reached ?? 0) > (cur.gates_reached ?? 0)
      || ((r.gates_reached ?? 0) === (cur.gates_reached ?? 0) && (r.turns ?? 0) < (cur.turns ?? 0))
    if (better) best.set(r.model, r)
  }
  return [...best.values()].sort((a, b) => (b.gates_reached ?? 0) - (a.gates_reached ?? 0) || (a.turns ?? 0) - (b.turns ?? 0))
}

// ── the data files ──────────────────────────────────────────────────────────

let boardPromise = null

/** Tests swap the fetcher and reset the board memo; production never calls this. */
export function _setFetch(fn) { fetchImpl = fn; boardPromise = null }
let fetchImpl = (...a) => fetch(...a)

async function loadJSON(rel) {
  // `no-cache` revalidates against Pages' 10-minute cache with a conditional
  // request, so a fresh publish shows up on reload instead of ten minutes later.
  const res = await fetchImpl(`${BASE}${rel}`, { headers: { Accept: 'application/json' }, cache: 'no-cache' })
  if (!res.ok) throw new Error(`GET ${BASE}${rel} → ${res.status}`)
  return res.json()
}

function board() {
  if (!boardPromise) boardPromise = loadJSON('data/leaderboard.json').catch(() => [])
  return boardPromise
}

const EMPTY = {
  '/api/models': [], '/api/configs': [], '/api/checkpoints': [], '/api/roms': [], '/api/starts': [],
  '/api/profiles': { version: null, reviewed: null, profiles: [], configs: [] },
  '/api/queue': { active: null, items: [], last_error: null },
  '/api/emulator/status': { configured: false, process_up: false, connected: false, busy: false, active_run_id: null },
}

/** Answer an `/api/*` GET from the published files. Mirrors the server routes api.js calls. */
export async function staticGet(path) {
  const url = new URL(path, 'http://static.local')
  const p = url.pathname
  if (p in EMPTY) return EMPTY[p]
  if (p === '/api/benchmarks') return loadJSON('data/benchmarks.json').catch(() => [])
  if (p === '/api/leaderboard') return rankBoard(await board(), url.searchParams.get('benchmark'))
  if (p === '/api/runs') return board()
  const m = p.match(/^\/api\/runs\/([^/]+)(?:\/(summary|trace))?$/)
  if (m) {
    const runId = decodeURIComponent(m[1])
    const row = (await board()).find((r) => r.run_id === runId)
    if (!m[2]) {
      if (!row) throw new Error(`GET ${path} → 404`)
      return row
    }
    // The default publish is result + video only: the row says so, and the
    // Report renders no turn section for a null trace — so don't even ask.
    if (m[2] === 'trace' && row && row.trace_published === false) return null
    return loadJSON(`data/runs/${encodeURIComponent(runId)}/${m[2]}.json`)
  }
  throw new Error(`GET ${path} → not available on the static site`)
}

/** A socket handle that never connects; callers only ever call close(). */
export const noSocket = () => ({ close() {} })
