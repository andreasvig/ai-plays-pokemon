// src/dashboard/web/src/lib/species.js — which picture and which name a
// species id means, given the cartridge that reported it.
//
// The defect these were written against, found 2026-09-21 on a Platinum card
// Andreas screenshotted: BattleCard built every sprite URL as
// `pokemon/${id}.png` against ONE file set keyed by the GEN-3 INTERNAL species
// index, and four of the seven cartridges report a NATIONAL DEX number instead.
// The rival's Chimchar (dex 390) drew gen-3 internal 390, which is Anorith, and
// Black's Snivy (495) drew nothing at all because that set stops at 411.
//
// Every case below is chosen so a wrong answer is VISIBLY wrong rather than
// merely different, and the pairs are the point:
//
//   286  Poochyena in gen-3 internal, Breloom in the National Dex. Measured
//        both ways — Emerald run 2026-09-20_17-59-44 turn 91 reads 286 with
//        POOCHYENA on screen, so the gen-3 games must keep resolving 286 into
//        the gen-3 set.
//   390  Anorith in gen-3 internal, Chimchar in the National Dex. Platinum run
//        2026-09-20_21-42-46 turn 83 reads 390 with CHIMCHAR on the plate.
//   16   Pidgey under BOTH numberings — the control that a game below 252
//        cannot distinguish the two, so it must not be used as evidence for
//        either and must keep working whatever the routing does.
//
// Ten of the twelve fail against the pre-fix behaviour (one file set, names
// from the per-game gen-3 roster only, no placeholder) — checked by running
// this file against a mutant module with exactly that behaviour. The two that
// do not are "an unnamed species stays a number" and "every cartridge … has a
// keyspace": the first pins the `#390` fallback that must SURVIVE the change
// and the second pins the join, so both are regression guards rather than
// claims about the fix.
import test from 'node:test'
import assert from 'node:assert/strict'
import { pokemonSpriteUrl, partySpriteUrl, speciesName, loadDexNames, _resetSpecies,
         SPECIES_KEYSPACE, UNKNOWN_SPRITE }
  from '../../src/dashboard/web/src/lib/species.js'

// `trainers/<game>/index.json` as scripts/extract_trainers.py writes it: names
// keyed by the GEN-3 INTERNAL index. Only the two gen-3 cartridges have one.
const emeraldRoster = { version: 3, game: 'emerald-us', species: { 286: 'Poochyena', 16: 'Pidgey' } }
// `pokemon/dex/index.json` as scripts/extract_dex_sprites.py writes it.
const dexIndex = { version: 1, keyspace: 'national-dex', species: { 286: 'Breloom', 390: 'Chimchar', 19: 'Rattata', 16: 'Pidgey' } }

test.beforeEach(() => { _resetSpecies() })

test('the same number is two different mons on two cartridges, and gets two different files', () => {
  // The whole bug in one assertion. A single keyspace — whichever one — makes
  // these two equal, so no implementation with one file set can pass it.
  assert.equal(pokemonSpriteUrl('emerald-us', 286), '/pokemon/286.png')
  assert.equal(pokemonSpriteUrl('platinum-us', 286), '/pokemon/dex/286.png')
  assert.notEqual(pokemonSpriteUrl('emerald-us', 286), pokemonSpriteUrl('platinum-us', 286))
})

test('all four DS cartridges resolve into the National Dex set', () => {
  // The ids are the ones actually recorded: Chimchar on Platinum, Sentret on
  // SoulSilver, Snivy on Black, Oshawott on Black 2.
  assert.equal(pokemonSpriteUrl('platinum-us', 390), '/pokemon/dex/390.png')
  assert.equal(pokemonSpriteUrl('soulsilver-us', 161), '/pokemon/dex/161.png')
  assert.equal(pokemonSpriteUrl('black-us', 495), '/pokemon/dex/495.png')
  assert.equal(pokemonSpriteUrl('black2-us', 501), '/pokemon/dex/501.png')
})

test('gen 5 numbers run past the end of the gen-3 set and must not fall off it', () => {
  // 495 is 84 past the last gen-3 internal index, so the old URL named a file
  // that does not exist and the card hid the image — a wild battle with no mon.
  assert.notEqual(pokemonSpriteUrl('black-us', 495), '/pokemon/495.png')
  assert.notEqual(pokemonSpriteUrl('black-us', 495), UNKNOWN_SPRITE)
})

test('the three older cartridges keep the file set that is already right', () => {
  assert.equal(pokemonSpriteUrl('firered-us', 4), '/pokemon/4.png')
  assert.equal(pokemonSpriteUrl('emerald-us', 277), '/pokemon/277.png')
  // Crystal's numbering IS the National Dex — gen 2 numbers that way — and 16
  // happens to mean Pidgey in both, so this is a control on the ROUTING and
  // not on the picture: whichever set it reads, it must read a real one.
  assert.equal(pokemonSpriteUrl('crystal-us', 16), '/pokemon/dex/16.png')
  assert.equal(SPECIES_KEYSPACE['crystal-us'], 'national-dex')
})

test('a game nobody has a keyspace for draws a question mark, not a guess', () => {
  // route.py already refuses to draw a route whose cartridge it cannot name on
  // some other cartridge's artwork; this is the same refusal for sprites. A
  // default-to-gen-3 fallback passes every other test here and fails this one.
  for (const game of [null, undefined, '', 'yellow-us']) {
    assert.equal(pokemonSpriteUrl(game, 390), UNKNOWN_SPRITE, `game ${game}`)
  }
})

test('an id outside its keyspace degrades to the placeholder, never to a broken image', () => {
  assert.equal(pokemonSpriteUrl('firered-us', 412), UNKNOWN_SPRITE)   // one past gen 3
  assert.equal(pokemonSpriteUrl('black-us', 650), UNKNOWN_SPRITE)     // one past gen 5
  assert.equal(pokemonSpriteUrl('platinum-us', 0), UNKNOWN_SPRITE)
  assert.equal(pokemonSpriteUrl('platinum-us', null), UNKNOWN_SPRITE)
  assert.equal(pokemonSpriteUrl('platinum-us', 'Chimchar'), UNKNOWN_SPRITE)
  assert.ok(UNKNOWN_SPRITE.endsWith('.png'), 'the placeholder has to be a real image')
})

test('a trainer party member is gen-3 internal whatever the card is drawing', () => {
  // The id comes from the pret roster, not from memory, so it is gen-3 internal
  // by construction — and the only cartridges with a roster are the gen-3 ones.
  assert.equal(partySpriteUrl(277), '/pokemon/277.png')
  assert.equal(partySpriteUrl(412), UNKNOWN_SPRITE)
  assert.equal(partySpriteUrl(null), UNKNOWN_SPRITE)
})

test('a dex-numbered cartridge names its mon instead of printing the number', () => {
  // Crystal's wild battles rendered "#19 · Lv 2" beside a picture of a Rattata
  // until 2026-09-21: the card only ever read the per-game gen-3 roster, which
  // Crystal does not have.
  assert.equal(speciesName('crystal-us', 19, { trainers: null, dex: dexIndex }), 'Rattata')
  assert.equal(speciesName('platinum-us', 390, { trainers: null, dex: dexIndex }), 'Chimchar')
})

test('a name is never taken from the other numbering', () => {
  // Emerald's 286 is Poochyena. Handing the card the dex table as well must not
  // make it say Breloom — the sprite bug wearing words.
  assert.equal(speciesName('emerald-us', 286, { trainers: emeraldRoster, dex: dexIndex }), 'Poochyena')
  assert.equal(speciesName('emerald-us', 286, { trainers: null, dex: dexIndex }), '#286')
  assert.equal(speciesName('platinum-us', 286, { trainers: emeraldRoster, dex: dexIndex }), 'Breloom')
})

test('an unnamed species stays a number', () => {
  assert.equal(speciesName('black-us', 649, { dex: dexIndex }), '#649')
  assert.equal(speciesName('yellow-us', 19, { dex: dexIndex }), '#19')
  assert.equal(speciesName('crystal-us', 19, {}), '#19')
  assert.equal(speciesName('crystal-us', null, { dex: dexIndex }), null)
})

test('the dex index is fetched once and refused when it is not the dex index', async () => {
  const seen = []
  globalThis.fetch = (url) => {
    seen.push(String(url))
    return Promise.resolve({ ok: true, json: () => Promise.resolve(dexIndex) })
  }
  assert.equal((await loadDexNames()).species['390'], 'Chimchar')
  await loadDexNames()
  assert.equal(seen.length, 1, 'the table is one file for all five dex games; fetch it once')
  assert.ok(seen[0].endsWith('pokemon/dex/index.json'), seen[0])

  // The dev server answers an unknown path with the SPA's index.html at 200,
  // so "it parsed" is not "it is the right file".
  _resetSpecies()
  globalThis.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ schema: 2, maps: {} }) })
  assert.equal(await loadDexNames(), null)

  _resetSpecies()
  globalThis.fetch = () => Promise.resolve({ ok: false, json: () => Promise.reject(new Error('404')) })
  assert.equal(await loadDexNames(), null)
})

test('every cartridge the referee has a contract for has a keyspace', () => {
  // tests/test_dex_sprites.py holds the other half of this join — that the
  // seven keys here are exactly configs/roms.yaml's. Repeated on this side so
  // adding a game to the table without a file set fails here too.
  const games = Object.keys(SPECIES_KEYSPACE)
  assert.equal(games.length, 7)
  for (const g of games) {
    assert.ok(['gen3-internal', 'national-dex'].includes(SPECIES_KEYSPACE[g]), g)
    assert.notEqual(pokemonSpriteUrl(g, 16), UNKNOWN_SPRITE, `${g} cannot resolve Pidgey`)
  }
})
