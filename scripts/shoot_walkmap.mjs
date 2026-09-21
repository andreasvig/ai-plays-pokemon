// Screenshot the "Where it walked" panel of a run, straight out of the running
// control center — the surface Andreas actually judges.
//
// Andreas, 2026-09-21: "we should add in teh plan an iteration cykle where you
// take creensht of threderd image in teh hsitry (where it walked tap) and make
// sure that teh viusal heavily aligne with the actual gaem screen shot".
//
// This is the first half of that loop: the picture of the panel as shipped.
// The second half — comparing it against the run's own emulator frames — is
// scripts/verify_maps.py. Kept apart on purpose: this one needs a browser and
// a live server, that one is pure pixels and runs in CI.
//
//   node scripts/shoot_walkmap.mjs <run_id> [<run_id>...]
//
// Writes artifacts/game-map-render/ui/<run_id>.png, plus -full.png for the
// whole detail panel so a battle card's sprite is in the same shot.
//
// Playwright is NOT a dependency of this repo — it is a ~400 MB browser
// download that only this one script wants, so it is resolved from wherever
// the machine already has it. `createRequire` rather than a bare import
// because ESM ignores NODE_PATH and a bare import would only ever find a
// local node_modules. Override with PLAYWRIGHT_PATH if it lives elsewhere.
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const req = createRequire(import.meta.url)
const CANDIDATES = [process.env.PLAYWRIGHT_PATH, 'playwright',
                    '/opt/homebrew/lib/node_modules/playwright'].filter(Boolean)
let chromium
for (const c of CANDIDATES) {
  try { ({ chromium } = req(c)); break } catch { /* next */ }
}
if (!chromium) {
  console.error(`playwright not found. Tried:\n  ${CANDIDATES.join('\n  ')}\n` +
                'Install it (npm i -g playwright && playwright install chromium) ' +
                'or set PLAYWRIGHT_PATH to its directory.')
  process.exit(2)
}

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const OUT = resolve(ROOT, 'artifacts/game-map-render/ui')
const BASE = process.env.POKEBENCH_URL || 'http://localhost:3420'

async function shoot(page, runId) {
  const notes = []
  await page.goto(`${BASE}/history/${runId}`, { waitUntil: 'networkidle' })
  // Two surfaces mount RouteMap: `section.map` in LevelDetail (a history row
  // expanded in place) and `section.mapsec` in Report (/history/<id>, where
  // this script lands). Accept either so the script survives a reshuffle.
  const section = page.locator('section.mapsec, section.map').first()
  try {
    await section.waitFor({ state: 'visible', timeout: 20000 })
  } catch {
    return { runId, ok: false, why: 'no "Where it walked" section on the page' }
  }
  const head = section.locator('.maphead')
  if ((await head.getAttribute('aria-expanded')) !== 'true') await head.click()
  // The atlas PNGs load after the click. Wait for the canvas to exist and for
  // every image the page has started to finish, then a beat for the draw.
  await section.locator('canvas').first().waitFor({ state: 'visible', timeout: 20000 })
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1200)
  // The viewer opens at its own zoom, which on a large map lands somewhere
  // arbitrary. "fit" is what a person clicks first, so shoot that: the whole
  // walked area is what we are judging, not wherever the viewer happened to
  // start. Report when the button is missing rather than shooting the corner.
  const fit = section.getByRole('button', { name: /^fit$/i }).first()
  if (await fit.count()) { await fit.click(); await page.waitForTimeout(900) }
  else notes.push('no "fit" button — shot at the viewer\'s default zoom')
  const blank = await section.locator('canvas').first().evaluate((c) => {
    const g = c.getContext('2d')
    if (!g || !c.width || !c.height) return 'no 2d context or zero-sized canvas'
    const d = g.getImageData(0, 0, c.width, c.height).data
    const first = [d[0], d[1], d[2], d[3]].join(',')
    for (let i = 4; i < d.length; i += 4) {
      if ([d[i], d[i + 1], d[i + 2], d[i + 3]].join(',') !== first) return null
    }
    return `canvas is one flat colour rgba(${first}) at ${c.width}x${c.height}`
  })
  if (blank) notes.push(blank)
  mkdirSync(OUT, { recursive: true })
  await section.screenshot({ path: `${OUT}/${runId}.png` })
  // Then one battle card, opened by clicking its marker on the map. The card
  // is the other half of what Andreas judges — it carries the species sprite,
  // the trainer and the outcome — and it exists only once a `.fight` button is
  // clicked, so a screenshot of the map alone never shows it.
  const fights = section.locator('button.fight')
  const n = await fights.count()
  if (!n) notes.push('no battle markers on the map')
  else {
    await fights.nth(Math.floor(n / 2)).click()
    const card = section.locator('.bcard').first()
    try {
      await card.waitFor({ state: 'visible', timeout: 5000 })
      await page.waitForTimeout(500)
      await card.screenshot({ path: `${OUT}/${runId}-battle.png` })
    } catch { notes.push(`clicked a battle marker of ${n} and no card opened`) }
  }
  return { runId, ok: true, notes }
}

const runs = process.argv.slice(2)
if (!runs.length) { console.error('usage: shoot_walkmap.mjs <run_id> [<run_id>...]'); process.exit(2) }
const browser = await chromium.launch()
// 2x so a 16px tile survives being read at a glance, and tall so a 96-tile map
// is one shot rather than a scroll.
const page = await browser.newPage({ viewport: { width: 1600, height: 2200 }, deviceScaleFactor: 2 })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
let bad = 0
for (const runId of runs) {
  const r = await shoot(page, runId)
  if (!r.ok) { bad++; console.log(`FAIL  ${runId}  ${r.why}`) }
  else console.log(`ok    ${runId}${r.notes.length ? '  !! ' + r.notes.join('; ') : ''}`)
}
if (errors.length) { console.log('\nbrowser errors:'); for (const e of [...new Set(errors)]) console.log('  ' + e) }
await browser.close()
console.log(`\nwrote ${OUT}`)
process.exit(bad ? 1 : 0)
