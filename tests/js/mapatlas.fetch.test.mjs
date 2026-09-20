// The atlas FETCHING layer: loadAtlas, loadTrainers, loadMapImage, mapImageUrl.
//
// None of it had a test. It is also where the cross-game collision lived: one
// module-level atlas promise, one flat `maps/` namespace, and an image cache
// keyed on the BARE FILENAME — so FireRed's `3-0.png` (Pallet Town) and
// Emerald's `3-0.png` were one cache entry and whichever loaded first won.
// route.py:63-68 and observed.py:316-320 both document and refuse that
// collision server-side; the browser had no equivalent refusal.
//
// The last test here fails on a URL-only fix, which is the point: correcting
// `mapImageUrl` while leaving `imgCache` keyed on the filename still serves
// Emerald a picture of Pallet Town.
import test from 'node:test'
import assert from 'node:assert/strict'
import { loadAtlas, loadTrainers, loadMapImage, mapImageUrl, trainerSpriteUrl, _resetAtlas }
  from '../../src/dashboard/web/src/lib/mapatlas.js'

/** Record every fetch and answer each URL from a table. */
function stubFetch(table) {
  const seen = []
  globalThis.fetch = (url) => {
    seen.push(String(url))
    const hit = Object.entries(table).find(([frag]) => String(url).includes(frag))
    if (!hit) return Promise.resolve({ ok: false, json: () => Promise.reject(new Error('404')) })
    return Promise.resolve({ ok: true, json: () => Promise.resolve(hit[1]) })
  }
  return seen
}

/** A stand-in Image that records every src assigned and never loads. */
function stubImage() {
  const made = []
  globalThis.Image = class {
    constructor() { made.push(this); this._src = null }
    set src(v) { this._src = v }
    get src() { return this._src }
  }
  return made
}

test.beforeEach(() => { _resetAtlas() })

test('two games fetch two different atlases and memoise independently', async () => {
  const seen = stubFetch({
    'maps/firered-us/index.json': { schema: 2, game: 'firered-us', maps: { '3:0': {} } },
    'maps/emerald-us/index.json': { schema: 2, game: 'emerald-us', maps: { '3:0': {} } },
  })
  const [f, e] = await Promise.all([loadAtlas('firered-us'), loadAtlas('emerald-us')])
  assert.equal(f.game, 'firered-us')
  assert.equal(e.game, 'emerald-us')
  assert.notEqual(f, e, 'one memo for both games was the original bug')

  // Second call is served from the memo, not the network.
  const before = seen.length
  await loadAtlas('firered-us')
  assert.equal(seen.length, before, 'the per-game memo must still memoise')
})

test('a null game fetches nothing and resolves null', async () => {
  const seen = stubFetch({})
  assert.equal(await loadAtlas(null), null)
  assert.equal(await loadAtlas(undefined), null)
  assert.equal(await loadTrainers(null), null)
  assert.equal(seen.length, 0, 'a route with no game must not guess a cartridge')
})

test('an atlas announcing a different game is refused', async () => {
  stubFetch({ 'maps/platinum-us/index.json': { schema: 2, game: 'firered-us', maps: { '3:0': {} } } })
  assert.equal(await loadAtlas('platinum-us'), null,
    'drawing one cartridge on another\'s artwork is the collision this whole split exists to stop')
})

test('HTML from the SPA catch-all is not mistaken for an atlas', async () => {
  // The dev server answers an unknown path with index.html at status 200, so a
  // game with no atlas yet gets HTML rather than a 404.
  globalThis.fetch = () => Promise.resolve({
    ok: true, json: () => Promise.reject(new SyntaxError('Unexpected token <')),
  })
  assert.equal(await loadAtlas('platinum-us'), null)
})

test('an atlas with no maps at all is not an atlas', async () => {
  stubFetch({ 'maps/black-us/index.json': { schema: 2, game: 'black-us' } })
  assert.equal(await loadAtlas('black-us'), null)
})

test('trainers are namespaced per game, like the maps', async () => {
  const seen = stubFetch({
    'trainers/firered-us/index.json': { trainers: { 414: { label: 'Brock' } } },
    'trainers/crystal-us/index.json': { trainers: { 414: { label: 'Someone else' } } },
  })
  const [a, b] = await Promise.all([loadTrainers('firered-us'), loadTrainers('crystal-us')])
  assert.equal(a.trainers['414'].label, 'Brock')
  assert.equal(b.trainers['414'].label, 'Someone else')
  assert.ok(seen.some((u) => u.includes('trainers/firered-us/')))
  assert.ok(seen.some((u) => u.includes('trainers/crystal-us/')))
  assert.notEqual(trainerSpriteUrl('firered-us', 'brock.png'), trainerSpriteUrl('crystal-us', 'brock.png'))
})

test('the same filename in two games is two URLs', () => {
  assert.notEqual(mapImageUrl('firered-us', '3-0.png'), mapImageUrl('emerald-us', '3-0.png'))
})

test('the same filename in two games is two cache entries', () => {
  // THE test. A URL-only fix passes everything above and fails this: the cache
  // was keyed on the bare filename, so the second game got the first game's
  // decoded image and never issued a request at all.
  const made = stubImage()
  const a = loadMapImage('firered-us', '3-0.png')
  const b = loadMapImage('emerald-us', '3-0.png')
  assert.notEqual(a, b, 'two games sharing one cache entry is the collision')
  assert.equal(made.length, 2, 'the second game must actually load its own image')
  assert.notEqual(made[0].src, made[1].src)
  assert.match(made[0].src, /maps\/firered-us\/3-0\.png$/)
  assert.match(made[1].src, /maps\/emerald-us\/3-0\.png$/)

  // The control: the SAME game and file is still cached, or every redraw
  // re-decodes every map.
  assert.equal(loadMapImage('firered-us', '3-0.png'), a)
  assert.equal(made.length, 2)
})
