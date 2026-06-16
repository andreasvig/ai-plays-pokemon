// Pure (no-svelte) windowing for the live spectate trace feed.
//
// rebuildFeed() in Spectate.svelte ran O(N) over ALL turns on every event, and
// the events WS replays the full backlog on (re)connect — so opening spectate
// on a long run was ~O(N^2) and hung, with the feed/Maps/DOM all growing
// unbounded. windowFeed() bounds the rendered feed to the last `maxTasks` tasks
// (or, when there are no bound TaskMaster cards, the last `fallbackTurns`
// turns), returning the cutoff so the caller can prune its accumulators too.
//
// Node-importable: no svelte imports, so the logic can be unit-checked directly.

export function windowFeed({ turnBoxes, masterCards, maxTasks = 3, fallbackTurns = 40 }) {
  // bound firstTurns of every master card that has been bound to a turn, asc.
  const boundFirstTurns = []
  for (const c of masterCards.values()) {
    if (c.firstTurn != null) boundFirstTurns.push(c.firstTurn)
  }
  boundFirstTurns.sort((a, b) => a - b)

  let cutoffTurn
  if (boundFirstTurns.length >= maxTasks) {
    // keep the last `maxTasks` tasks: cut at the firstTurn that opens the window
    cutoffTurn = boundFirstTurns[boundFirstTurns.length - maxTasks]
  } else if (boundFirstTurns.length >= 1) {
    // fewer tasks than the window → keep them all
    cutoffTurn = 0
  } else {
    // no bound masters (casual / no-TaskMaster) → window by raw turn count
    let maxTurn = 0
    for (const t of turnBoxes.keys()) if (t > maxTurn) maxTurn = t
    cutoffTurn = Math.max(0, maxTurn - fallbackTurns)
  }

  // master cards keyed by their (bound) first turn, rendered just BEFORE that
  // turn's block — same shape/order as the original rebuildFeed().
  const mastersByTurn = new Map()
  for (const c of masterCards.values()) {
    if (c.firstTurn != null) mastersByTurn.set(c.firstTurn, c)
  }

  const turns = [...turnBoxes.keys()].sort((a, b) => a - b)
  const feed = []
  let hiddenTurns = 0
  for (const t of turns) {
    if (t < cutoffTurn) { hiddenTurns += 1; continue }
    const m = mastersByTurn.get(t)
    if (m) feed.push({ kind: 'master', id: 'm' + m.taskIndex, ...m })
    feed.push({ kind: 'turn', id: 't' + t, turn: t, boxes: turnBoxes.get(t) })
  }

  return { feed, cutoffTurn, hiddenTurns }
}
