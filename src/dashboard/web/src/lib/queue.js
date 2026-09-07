// Pure queue-payload mapping. No svelte, no DOM, no imports — so
// tests/js/queue.test.mjs can check it under plain node, the same arrangement
// lib/live.js and lib/feed.js have. api.js (which imports router.svelte.js and
// is therefore NOT node-importable) delegates here rather than mapping inline.
//
// The one thing in here with rules: `GET /api/queue`'s `last_error`. The
// executor records it when an item was dequeued and then never became a run —
// an illegal thinking level for the model's provider profile, a named variant
// on a legacy config, a ROM that won't load, a recorder that won't boot.
// `drain_loop` swallows those so one poisoned item cannot freeze the serial
// queue, which also means the card flashes active and then vanishes with
// nothing said anywhere. The route has served this field since the queue
// existed and no component read it (finding #5b of the append-standard review).

/** snake_case `last_error` → the camelCase shape the strip renders. null → null. */
export function toQueueError(raw) {
  if (!raw || typeof raw !== 'object') return null
  return {
    queueId: raw.queue_id ?? null,
    kind: raw.kind ?? null,
    // Already the ALIAS the run was queued under ("kimi-k3(high)"), not the raw
    // OpenRouter id — the executor records `item.model` verbatim.
    model: raw.model ?? null,
    config: raw.config ?? null,
    providerProfile: raw.provider_profile ?? null,
    error: raw.error ?? '',
    // The dismissal identity. A dismissed strip must not come back on the next
    // poll, and a NEW failure must — so the UI remembers this string, not a
    // boolean. The executor stamps it once per failure.
    at: raw.at ?? null,
  }
}

/** The one-line "which run" summary shown under the headline. '' when unknown. */
export function queueErrorSubject(err) {
  if (!err) return ''
  const bits = []
  if (err.model) bits.push(err.model)
  if (err.config) bits.push(err.config)
  if (err.providerProfile) bits.push(err.providerProfile)
  if (err.queueId) bits.push(err.queueId)
  return bits.join(' · ')
}
