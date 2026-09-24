// One rule, enforced structurally: a component never reads a run's cost fields
// directly — it asks `costOf`/`costCell` in board.js, which is the only thing
// that knows whether the figure is a bill or a derivation from a list price.
//
// This exists because the unit tests could not catch the way it broke. The
// Methodology matrix prices a leg as `turns × the run's own per-turn rate`, and
// that multiplication lives in the .svelte file, outside every tested module.
// It kept reading `row.avgCostPerTurn` — $0.0003 of OCR on a run that was billed
// nothing — while the cell around it had already grown the ≈ that says "derived
// from the model's list price". The mark ended up on the billed number, which is
// worse than either mistake alone, and it was only visible in the rendered table
// (2026-09-18).
//
// So the guard is on the SHAPE of the code, not on an output: any new surface
// that reaches for the raw field fails here, whether or not anyone thought to
// write a fixture for it.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const WEB = join(dirname(fileURLToPath(import.meta.url)), '../../src/dashboard/web/src')
const RAW = /(?<![\w.])(?:row|r|run|x\.row|s\.row)\s*(?:\?\.)?\.\s*(totalCostUsd|avgCostPerTurn)\b/

function offenders(dir) {
  const out = []
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, name.name)
    if (name.isDirectory()) { out.push(...offenders(path)); continue }
    if (!name.name.endsWith('.svelte')) continue
    readFileSync(path, 'utf8').split('\n').forEach((line, i) => {
      // A comment may name the field — that is how the rule explains itself.
      const code = line.replace(/\/\/.*$/, '').replace(/<!--.*?-->/g, '')
      if (RAW.test(code)) out.push(`${name.name}:${i + 1}: ${line.trim()}`)
    })
  }
  return out
}

test('no component reads totalCostUsd or avgCostPerTurn off a row', () => {
  const hits = offenders(join(WEB, 'components'))
  assert.deepEqual(hits, [], 'go through costOf/costCell in board.js instead:\n' + hits.join('\n'))
})

test('the guard actually matches the shape it is guarding against', () => {
  // Without this, a typo in the regex makes the test above pass on everything.
  assert.ok(RAW.test('turns * (x.row.avgCostPerTurn ?? 0)'), 'the Methodology bug')
  assert.ok(RAW.test('{usd(r.totalCostUsd)}'), 'a table cell')
  assert.ok(RAW.test('isFree(row) ? NO_PRICE : usd(row.totalCostUsd)'), 'the old pattern')
  // And does not fire on the things that are fine.
  assert.ok(!RAW.test('const c = costOf(r)'), 'the approved route')
  assert.ok(!RAW.test('{c.total}'), 'a formatted result')
  assert.ok(!RAW.test('costPer10(c.perTurn)'), 'a value already taken from costOf')
})
