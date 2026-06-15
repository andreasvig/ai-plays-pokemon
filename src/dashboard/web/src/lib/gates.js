// The pokebench-v1 gate ladder, flattened (the Misty+Bill multigate's two
// members appear inline, tagged with `group`). Mirrors
// configs/checkpoints-firered-v1.yaml. WIP until launch — read, never hardcode
// downstream; this is the mock's copy for rendering progress.
export const GATES = [
  { id: 'left_bedroom',            name: 'Left the bedroom',                 deadline: 25 },
  { id: 'left_house',              name: 'Stepped outside in Pallet Town',   deadline: 50 },
  { id: 'oaks_lab_entered',        name: "Entered Oak's Lab",                deadline: 75 },
  { id: 'starter_chosen',          name: 'Chose a starter',                  deadline: 100 },
  { id: 'rival1_done',             name: 'First rival battle done',          deadline: 125 },
  { id: 'route1_reached',          name: 'Reached Route 1',                  deadline: 150 },
  { id: 'viridian_reached',        name: 'Reached Viridian City',            deadline: 200 },
  { id: 'parcel_delivered',        name: "Delivered Oak's Parcel",           deadline: 250 },
  { id: 'pokedex_received',        name: 'Received the Pokédex',             deadline: 300 },
  { id: 'viridian_forest_reached', name: 'Entered Viridian Forest',          deadline: 350 },
  { id: 'pewter_reached',          name: 'Reached Pewter City',              deadline: 400 },
  { id: 'brock_defeated',          name: 'Defeated Brock (Boulder Badge)',   deadline: 500, badge: true },
  { id: 'route3_reached',          name: 'Reached Route 3',                  deadline: 550 },
  { id: 'mt_moon_entered',         name: 'Entered Mt. Moon',                 deadline: 600 },
  { id: 'mt_moon_cleared',         name: 'Cleared Mt. Moon',                 deadline: 700 },
  { id: 'cerulean_reached',        name: 'Reached Cerulean City',            deadline: 700 },
  { id: 'cascade_badge',           name: 'Defeated Misty (Cascade Badge)',   deadline: 800, badge: true, group: 'misty_bill' },
  { id: 'bills_errand_reached',    name: "Reached Route 25 (Bill's house)",  deadline: 900, group: 'misty_bill' },
  { id: 'vermilion_reached',       name: 'Reached Vermilion City',           deadline: 1000 },
  { id: 'ss_anne_boarded',         name: 'Boarded the S.S. Anne',            deadline: 1100 },
  { id: 'thunder_badge',           name: 'Defeated Lt. Surge (Thunder Badge)', deadline: 1200, badge: true },
]

export const GATE_INDEX = Object.fromEntries(GATES.map((g, i) => [g.id, i]))
export const TOTAL_GATES = GATES.length

export function gate(id) { return GATES[GATE_INDEX[id]] }
export function gatesReached(furthestId) {
  return furthestId == null ? 0 : GATE_INDEX[furthestId] + 1
}
