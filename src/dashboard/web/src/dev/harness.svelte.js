// DEV-ONLY replay harness for SimpleView (build plan §9).
//
// The project has no JS test runner — package.json carries only dev/build/
// preview — so browser behaviour cannot be unit-tested. What replaces it is
// this: replay 20 real turns as synthetic `lastEvent` pushes with realistic
// phase timing, then drive the page with headless Chrome and read the
// component's data-* attributes instead of eyeballing screenshots. That loop
// caught three real bugs during the design session.
//
//   npm run dev  →  http://localhost:5173/harness.html
//   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
//     --headless --dump-dom --virtual-time-budget=240000 \
//     "http://localhost:5173/harness.html?think=400&exec=20&settle=120"
//
// Everything the assertions need ends up inside <pre id="log"> and
// <pre id="summary">, so a single --dump-dom captures the whole HISTORY, not
// just the final frame. Also on window.__harness for an interactive session.
//
// This file is not referenced by src/main.js and harness.html is not the Vite
// build input, so none of it reaches `npm run build`.
import { mount } from 'svelte'
import SimpleView from '../components/SimpleView.svelte'
import TURNS from './turns.json'

const qs = new URLSearchParams(location.search)
const num = (k, d) => (qs.has(k) ? Number(qs.get(k)) : d)

const CFG = {
  think: num('think', 2200), // turn_start → llm_output
  exec: num('exec', 190), // ms per button press
  settle: num('settle', 700), // press end → screen_settled
  gap: num('gap', 120), // screen_settled → next turn_start
  box: num('box', 24), // --boxh override, %
  startTurn: qs.has('turn') ? Number(qs.get('turn')) : null,
  seedTurn: qs.has('seed') ? Number(qs.get('seed')) : null, // mid-run entry (§8)
  resizeAt: qs.has('resizeat') ? Number(qs.get('resizeat')) : null, // ratchet reset
  loop: qs.get('loop') === '1',
  once: qs.get('once') === '1', // stop after ONE turn (jump-to-turn inspection)
  shots: qs.get('shots') === '1', // use the run's real screenshot URLs
}

// A stand-in screen so the harness renders identically whether or not the
// control center on :3420 is up. 240x160 is the GBA frame.
function placeholder(turn) {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160">` +
    `<rect width="240" height="160" fill="#2b2f26"/>` +
    `<rect x="8" y="8" width="224" height="144" fill="none" stroke="#6f7a63"/>` +
    `<text x="120" y="86" fill="#c9d2bc" font-family="monospace" font-size="26"` +
    ` text-anchor="middle">turn ${turn}</text></svg>`
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg)
}

// ── the observability sink ───────────────────────────────────────────────
const logEl = document.createElement('pre')
logEl.id = 'log'
const sumEl = document.createElement('pre')
sumEl.id = 'summary'
const hud = document.createElement('div')
hud.id = 'hud'
for (const el of [logEl, sumEl]) {
  el.style.cssText = 'position:fixed;left:-99999px;top:0;white-space:pre'
}
hud.style.cssText =
  'position:fixed;z-index:99;top:0;right:0;padding:6px 10px;background:rgba(10,11,12,.92);' +
  'color:#8fb6f5;font:12px ui-monospace,monospace;border-bottom-left-radius:8px'
document.body.append(logEl, sumEl, hud)

const t0 = performance.now()
const at = () => Math.round(performance.now() - t0)
const LOG = []
window.__harness = { log: LOG, records: [], cfg: CFG }

function log(kind, extra) {
  const row = { t: at(), kind, ...extra }
  LOG.push(row)
  logEl.textContent += JSON.stringify(row) + '\n'
}

// ── mount ────────────────────────────────────────────────────────────────
// ?seed=N models toggling the simple view on mid-run (plan §8): Spectate hands
// over the last turn it already has in `turnBoxes` and the box must be
// populated immediately, with no animation.
const seedTurn = CFG.seedTurn == null ? null : TURNS.find((t) => t.turn === CFG.seedTurn)
const props = $state({
  frame: seedTurn ? placeholder(seedTurn.turn) : null,
  lastEvent: null,
  seed: seedTurn
    ? { turn: seedTurn.turn, presses: seedTurn.action, reasoning: seedTurn.reasoning }
    : null,
  onexit: () => log('exit', {}),
})
mount(SimpleView, { target: document.getElementById('app'), props })

let seq = 0
const push = (data) => {
  props.lastEvent = { seq: ++seq, data }
  log('event', { type: data.type, turn: data.turn })
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const stage = () => document.querySelector('.stage')

// Every data-* transition is recorded, so the phase trace and the ratchet
// sequence are both reconstructable from one DOM dump.
function watch() {
  const el = stage()
  if (!el) return
  if (CFG.box !== 24) el.style.setProperty('--boxh', CFG.box + '%')
  const snap = () => ({
    phase: el.dataset.phase,
    turn: el.dataset.turn,
    fit: el.dataset.fit,
    clipped: el.dataset.clipped,
    pending: el.dataset.pending,
  })
  let prev = snap()
  log('attr', prev)
  hud.textContent = `${prev.phase} · fit ${prev.fit} · clip ${prev.clipped}`
  new MutationObserver(() => {
    const now = snap()
    if (JSON.stringify(now) === JSON.stringify(prev)) return
    prev = now
    log('attr', now)
    hud.textContent = `${now.phase} · fit ${now.fit} · clip ${now.clipped}`
  }).observe(el, { attributes: true, attributeFilter: ['data-phase', 'data-turn', 'data-fit', 'data-clipped', 'data-pending'] })
}

async function until(pred, what, timeout = 8000) {
  const end = performance.now() + timeout
  while (performance.now() < end) {
    if (pred()) return true
    await sleep(16)
  }
  // Loudly, not silently: a timeout here means the component fell behind the
  // event stream, and the sample taken after it describes the WRONG turn.
  log('TIMEOUT', { waiting_for: what, after_ms: timeout })
  return false
}

// ── the replay ───────────────────────────────────────────────────────────
async function replay() {
  await sleep(0)
  watch()
  let i = 0
  if (CFG.startTurn != null) {
    const k = TURNS.findIndex((t) => t.turn === CFG.startTurn)
    i = k >= 0 ? k : 0
  }
  let served = 0
  for (;;) {
    const t = TURNS[i]
    if (CFG.resizeAt === t.turn) {
      // The fit ceiling is frame-relative, so a resize invalidates the ratchet
      // and it is allowed to grow back exactly once, here.
      log('resize', { before: stage()?.dataset.fit })
      dispatchEvent(new Event('resize'))
      await sleep(50)
      log('resize-after', { fit: stage()?.dataset.fit })
    }
    const presses = String(t.action || '')
      .replace(/[[\]]/g, '')
      .split(/[,\s]+/)
      .filter(Boolean)

    props.frame = CFG.shots ? t.shot : placeholder(t.turn)
    push({ type: 'turn_start', turn: t.turn })
    await sleep(CFG.think)

    // Real shape: args is the decision JSON, reasoning + inputs inside it.
    push({
      type: 'llm_output',
      turn: t.turn,
      args: { inputs: presses, reasoning: t.reasoning, last_turn_succeeded: t.ok },
    })

    // The component reaches `executing` only after morph + presses + text.
    const settled = await until(
      () => stage()?.dataset.phase === 'executing' && stage()?.dataset.turn === String(t.turn),
      `executing@turn${t.turn}`
    )
    const el = stage()
    const rec = {
      turn: t.turn,
      presses: presses.length,
      chars: (t.reasoning || '').length,
      fit: el?.dataset.fit,
      clipped: el?.dataset.clipped,
      pending: el?.dataset.pending,
      stale: settled ? undefined : true,
    }
    window.__harness.records.push(rec)
    log('turn', rec)
    sumEl.textContent = JSON.stringify(window.__harness.records, null, 0)

    // EXECUTING: buttons pressed, then the screen settles. The pending box
    // must be gone for this whole window — sample it mid-way to prove it.
    const execMs = presses.length * CFG.exec + CFG.settle
    await sleep(Math.max(1, Math.floor(execMs / 2)))
    log('exec-probe', {
      phase: stage()?.dataset.phase,
      pending: stage()?.dataset.pending,
      pendingOpacity: getComputedStyle(document.querySelector('.pending')).opacity,
    })
    await sleep(Math.max(1, execMs - Math.floor(execMs / 2)))
    // Ignored by the component on purpose — turn.py logs button_sequence AFTER
    // press_button_list() returns, so it marks the END of pressing, not the
    // start. It is replayed here precisely so "the component ignores it" is
    // something the trace can show, not something you have to take on trust.
    push({ type: 'button_sequence', turn: t.turn, buttons: presses })
    // Separate task, deliberately: two writes to `lastEvent` inside one
    // synchronous block flush as a single Svelte update and the first event is
    // lost. Real WS frames arrive one per task, so this models the wire, and
    // pushing them back-to-back would be the harness lying about it.
    await sleep(4)
    push({ type: 'screen_settled', turn: t.turn, duration: 0.9 })
    await sleep(CFG.gap)

    served++
    i++
    if (CFG.once) break
    if (i >= TURNS.length) {
      if (!CFG.loop) break
      i = 0
    }
  }
  const done = document.createElement('div')
  done.id = 'done'
  done.dataset.turns = String(served)
  done.dataset.clippedAny = String(window.__harness.records.some((r) => r.clipped !== 'false'))
  done.dataset.fits = window.__harness.records.map((r) => r.fit).join(',')
  done.style.cssText = 'position:fixed;left:-99999px'
  document.body.appendChild(done)
  log('done', { turns: served })
}

replay()
