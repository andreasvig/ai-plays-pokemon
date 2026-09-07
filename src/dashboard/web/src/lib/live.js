// Pure (no-svelte) event → trace-box mapping for the LIVE spectate feed.
//
// Spectate.svelte owns the sockets, the stats row and the panels. This module
// owns the part worth testing on its own: which box a raw event becomes, and
// WHERE it goes. There are two destinations, not one:
//
//   * a GAMEPLAY TURN — numbered, keyed by `evt.turn`.
//   * a COMPACTION BLOCK — its own row with NO turn number, opened by
//     `compaction_start` and closed by `compaction_complete`, sitting between
//     the turn it summarises (`after_turn`) and the turn that follows it.
//
// The compaction block mirrors the run report exactly — see
// `trace_build._add_conversation_timeline` (which pulls every
// `compaction_*`/`phase == "compaction"` event out of the turn it landed on and
// emits an unnumbered `{kind: "compaction", number, after_turn}` row before that
// turn) and Report.svelte's `t.kind === 'compaction'` row. A compaction is not a
// game turn: it has its OWN counter, and it must never move the Turn stat.
//
// Node-importable (no svelte imports), so every rule below is unit-checked
// directly — see tests/js/live.test.mjs.

// ── per-request transport diagnostics (finding #2) ────────────────────────
// `llm_request_usage` used to be pushed as box kind `settle`, whose label is the
// literal "Screen settling" — an emulator wait. That mislabelled every model
// request on every append turn. It gets its own kind, and NOTHING but
// screen_settling/screen_settled may render as `settle` again.
export function usageMeta(evt) {
  const bits = [evt.phase || 'request']
  if (evt.attempt > 1) bits.push(`attempt ${evt.attempt}`)
  if (evt.provider) bits.push(String(evt.provider))
  return bits.join(' · ')
}

// The replay counter counts BLOCKS across three fields (reasoning, reasoning_details text,
// reasoning_details encrypted), so one earlier turn contributes 1–3 of them. Name the turns too,
// or "12/12" on turn 6 reads as a bug (2026-09-07, gemini-3.5-flash-lite).
export function replayLabel(c) {
  const expected = c?.expected_blocks ?? 0
  const replayed = c?.replayed_blocks ?? 0
  const turns = new Set((c?.manifest || []).map((m) => m.message)).size
  const state = c?.local_replay || 'unknown'
  if (expected === 0) return `reasoning replay none yet (${state})`
  const from = turns ? ` from ${turns} earlier turn${turns === 1 ? '' : 's'}` : ''
  return `reasoning replay ${replayed}/${expected} blocks${from} (${state})`
}

export function usageBox(evt) {
  const cache =
    evt.cache_read_fraction == null
      ? 'unknown'
      : `${(evt.cache_read_fraction * 100).toFixed(1)}% of input`
  const c = evt.continuity || {}
  const t = [
    `cache ${cache}`,
    replayLabel(c),
    `provider feedback: ${c.provider_feedback || 'not reported'}`,
  ].join(' · ')
  return { k: 'diag', t, meta: usageMeta(evt) }
}

// ── opaque reasoning (finding #22) ───────────────────────────────────────
// The endpoint billed reasoning tokens but returned no readable thinking (an
// `reasoning.encrypted` block has no text). Rendering nothing reads as "the
// model didn't think"; say who withheld it instead. The box is provisional: it
// is dropped the moment a real thinking event for the same request arrives,
// because usage is emitted BEFORE the thinking blocks of its own request
// (append_agent.py emits llm_request_usage, then llm_thinking, in that order).
export function withheldBox(evt) {
  const n = Number(evt.reasoning_tokens) || 0
  return {
    k: 'withheld',
    t: `Reasoning withheld by endpoint (${n.toLocaleString('en-US')} tokens)`,
    provisional: true,
  }
}

export function hasWithheldReasoning(evt) {
  return Number(evt.reasoning_tokens) > 0
}

// ── collapsed-turn preview (finding #22) ─────────────────────────────────
// The rail's collapsed rows previewed `thinking` and nothing else, so a turn
// whose endpoint returned no readable reasoning rendered a bare "…". Falls
// through to what the turn actually said, then to why nothing was said.
export function turnPreview(boxes, limit = 52) {
  const pick = (k) => (boxes || []).find((b) => b.k === k)
  const src =
    pick('thinking')?.t ||
    pick('output')?.reasoning ||
    pick('output')?.t ||
    pick('withheld')?.t ||
    pick('error')?.t ||
    ''
  const flat = String(src).replace(/\s+/g, ' ').trim()
  if (!flat) return ''
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat
}

// ── memory panel (finding #15) ───────────────────────────────────────────
// An append run writes memory ONLY at a compaction, so the panel is a
// full-width "(empty)" for the whole first segment (10 turns by default in config-5.0). Say
// what is actually going on instead. `null` = no compaction on this run
// (legacy per-turn memory edits), so the caller keeps its own empty text.
export function memoryHint(cfg) {
  const n = Number(cfg?.compaction?.every_n_turns)
  if (!Number.isFinite(n) || n <= 0) return null
  return `Memory is written at compaction · first compaction after turn ${n}`
}

// ── compaction block presentation ────────────────────────────────────────
export function compactionLabel(block) {
  return `Compaction ${block.number} · after turn ${block.afterTurn}`
}

/**
 * Seconds this compaction has been running (live) or took (finished).
 *
 * Prefers the EVENTS' own timestamps (RunLogger stamps every event with
 * `timestamp`, epoch seconds) over the client clock, because the client clock
 * is wrong for replayed history: the events WS re-sends the whole backlog from
 * cursor 0 on every reconnect, so a compaction that took four minutes an hour
 * ago is ingested start-and-complete in the same millisecond and would read
 * "0.0s". Falls back to the client clock only when an event carried no
 * timestamp at all.
 */
export function compactionElapsedS(block, nowMs) {
  if (block.complete) {
    if (block.startedTs != null && block.endedTs != null) {
      return Math.max(0, block.endedTs - block.startedTs)
    }
    if (block.startedAtMs == null || block.endedAtMs == null) return null
    return Math.max(0, (block.endedAtMs - block.startedAtMs) / 1000)
  }
  if (nowMs == null) return null
  if (block.startedTs != null) return Math.max(0, nowMs / 1000 - block.startedTs)
  if (block.startedAtMs == null) return null
  return Math.max(0, (nowMs - block.startedAtMs) / 1000)
}

/**
 * Accumulator for the live feed: gameplay turns and compaction blocks.
 *
 * `ingest(evt)` returns true when the feed changed and a rebuild is due.
 * Everything the caller renders hangs off `turnBoxes` (turn → boxes[]) and
 * `compactionsByTurn` (the turn a block sits BEFORE → blocks[]).
 */
export class LiveTrace {
  constructor({ now = () => Date.now() } = {}) {
    this._now = now
    this.reset()
  }

  reset() {
    this.turnBoxes = new Map()
    this.compactionsByTurn = new Map()
    this.currentTurn = 0
    this.count = 0
    // Reconnect safety: the events WS replays the WHOLE backlog from cursor 0
    // on every reconnect, so a block must be recognised as one already held
    // rather than opened twice. Keyed on the run's own identity for the
    // compaction (segment + after_turn), never on arrival order.
    this._byKey = new Map()
    this._open = null
  }

  // ── internals ──
  _boxes(turn) {
    const t = typeof turn === 'number' ? turn : this.currentTurn
    if (!this.turnBoxes.has(t)) this.turnBoxes.set(t, [])
    return this.turnBoxes.get(t)
  }

  _push(turn, box) {
    this._boxes(turn).push(box)
    return true
  }

  /**
   * Append a box to a gameplay turn from outside `ingest`.
   *
   * `llm_output` stays in the component because it also drives the TaskMaster
   * handback and the simple view's seed; everything else it needs is here.
   * `turn` may be undefined — it then lands on the turn in flight.
   */
  push(turn, box) {
    return this._push(turn, box)
  }

  /** The compaction block a `phase: "compaction"` event belongs to. */
  _blockFor(evt) {
    if (this._open) return this._open
    // A compaction is strictly sequential (the agent awaits it inside play()),
    // so "the open one" is unambiguous. If it has already closed — a stray
    // late attempt — fall back to the last block on the same turn rather than
    // silently dropping a billed request.
    const blocks = this.compactionsByTurn.get(evt.turn)
    return blocks && blocks.length ? blocks[blocks.length - 1] : null
  }

  _dropProvisional(list) {
    for (let i = list.length - 1; i >= 0; i -= 1) {
      if (list[i].provisional) {
        list.splice(i, 1)
        return true
      }
    }
    return false
  }

  // ── the event taxonomy ──
  ingest(evt) {
    const t = evt.type

    if (t === 'turn_start') {
      if (typeof evt.turn === 'number') this.currentTurn = evt.turn
      this._boxes(evt.turn)
      return true
    }

    // ── compaction block: its own row, no turn number ──
    if (t === 'compaction_start') {
      const key = `${evt.segment ?? '?'}:${evt.after_turn ?? evt.turn}`
      const held = this._byKey.get(key)
      if (held) {
        this._open = held.complete ? null : held
        return false
      }
      this.count += 1
      const block = {
        kind: 'compaction',
        id: `c${this.count}`,
        number: this.count,
        // `after_turn` is the last COMPLETED gameplay turn — the number the
        // report's row shows. `beforeTurn` is where the row sits in the feed:
        // compaction runs inside the next turn's play(), after its turn_start.
        afterTurn: evt.after_turn ?? (typeof evt.turn === 'number' ? evt.turn - 1 : null),
        beforeTurn: typeof evt.turn === 'number' ? evt.turn : this.currentTurn,
        reason: evt.reason || '',
        startedAtMs: this._now(),
        endedAtMs: null,
        // The producer's own clock (logger._log_event stamps every event).
        startedTs: typeof evt.timestamp === 'number' ? evt.timestamp : null,
        endedTs: null,
        complete: false,
        summary: '',
        memory: null,
        previousMemory: null,
        boxes: [],
      }
      this._byKey.set(key, block)
      const list = this.compactionsByTurn.get(block.beforeTurn) || []
      list.push(block)
      this.compactionsByTurn.set(block.beforeTurn, list)
      this._open = block
      return true
    }

    if (t === 'compaction_complete') {
      const block = this._blockFor(evt)
      this._open = null
      if (!block) return false
      if (block.complete) return false
      block.complete = true
      block.endedAtMs = this._now()
      if (typeof evt.timestamp === 'number') block.endedTs = evt.timestamp
      block.summary = evt.handover?.continuation_summary || ''
      block.memory = evt.handover?.memory ?? null
      block.previousMemory = evt.previous_memory ?? null
      return true
    }

    if (t === 'compaction_thinking') {
      const block = this._blockFor(evt)
      if (!block) return false
      this._dropProvisional(block.boxes)
      block.boxes.push({ k: 'thinking', t: evt.content || '' })
      return true
    }

    if (t === 'llm_request_usage') {
      const box = usageBox(evt)
      const withheld = hasWithheldReasoning(evt) ? withheldBox(evt) : null
      if (evt.phase === 'compaction') {
        const block = this._blockFor(evt)
        if (block) {
          block.boxes.push(box)
          if (withheld) block.boxes.push(withheld)
          return true
        }
      }
      this._push(evt.turn, box)
      if (withheld) this._push(evt.turn, withheld)
      return true
    }

    if (t === 'llm_request_error') {
      const box = { k: 'error', t: `${evt.phase}: ${evt.error}` }
      if (evt.phase === 'compaction') {
        const block = this._blockFor(evt)
        if (block) {
          block.boxes.push(box)
          return true
        }
      }
      return this._push(evt.turn, box)
    }

    // ── gameplay turn boxes ──
    if (t === 'llm_thinking') {
      const list = this._boxes(evt.turn)
      this._dropProvisional(list)
      list.push({ k: 'thinking', t: evt.content || '' })
      return true
    }

    if (t === 'memory_update_output') {
      // Finding #20 — one Memory box per compaction, not two. On an append run
      // turn.py logs `memory_update_output` immediately after
      // `compaction_complete` with the SAME memory and the same turn, so the
      // compaction block's own handover view already shows it. Legacy runs
      // never emit compaction_complete, so their real per-turn memory edits
      // still render. Keyed on the block, never on a config name.
      const blocks = this.compactionsByTurn.get(evt.turn)
      if (blocks && blocks.some((b) => b.complete)) return false
      const raw = evt.content || '(no changes)'
      let display = raw
      if (raw !== '(no changes)' && raw.toLowerCase() !== 'none') {
        try {
          display = JSON.stringify(JSON.parse(raw))
        } catch {
          /* keep raw */
        }
      }
      return this._push(evt.turn, { k: 'memory', t: display })
    }

    if (t === 'endpoint_warning') {
      // Finding #21 — reached only terminal.log before. It carries no `turn`,
      // so it lands on the turn in flight (`_boxes` falls back to currentTurn).
      return this._push(evt.turn, {
        k: 'warning',
        t: evt.message || `${evt.endpoint || 'endpoint'}: ${evt.kind || 'warning'}`,
        meta: evt.endpoint || '',
      })
    }

    if (t === 'budget_exhausted') {
      // Finding #16 — the run is OVER; it rendered nothing at all before.
      const spent = Number(evt.spent_usd)
      const cap = Number(evt.max_spend_usd)
      const amounts =
        Number.isFinite(spent) && Number.isFinite(cap)
          ? ` — $${spent.toFixed(4)} of $${cap.toFixed(4)}`
          : ''
      return this._push(evt.turn, {
        k: 'terminal',
        t: `Spend budget reached${amounts}. Run ends at this turn boundary.`,
      })
    }

    if (t === 'screen_settled') {
      return this._push(evt.turn, { k: 'settle', t: `Settled in ${evt.duration || 0}s` })
    }

    if (t === 'ocr_flush') {
      const n = evt.n_captures || 0
      const cleaned = evt.cleaned || ''
      if (n === 0 && !cleaned) return false
      const cost = evt.cost_usd ? ` · $${Number(evt.cost_usd).toFixed(5)}` : ''
      return this._push(evt.turn, {
        k: 'ocr',
        t: cleaned || '(empty)',
        meta: `${n} captures · ${evt.duration || 0}s${cost}`,
      })
    }

    if (t === 'llm_text') {
      return this._push(evt.turn, { k: 'output', ok: null, t: evt.content || '' })
    }

    if (t === 'tool_call') {
      return this._push(evt.turn, {
        k: 'tool',
        name: evt.tool,
        args: JSON.stringify(evt.args),
        resp: null,
      })
    }

    if (t === 'tool_response') {
      const resp = typeof evt.response === 'string' ? evt.response : JSON.stringify(evt.response)
      return this._push(evt.turn, { k: 'tool', name: '↳ response', args: '', resp })
    }

    // Per-attempt LLM call retries (timeout / transient provider error). The
    // backend re-rolls the provider with escalating routing; surface each
    // attempt LOUDLY so a stalling turn is obvious live, not a silent freeze.
    if (t === 'agent_retry') {
      const n = evt.attempt
      const max = evt.max_attempts
      const why =
        evt.error_type === 'TimeoutError'
          ? `timed out after ${Math.round(evt.timeout_s || 0)}s`
          : `${evt.error_type || 'error'}${evt.error ? ` (${String(evt.error).slice(0, 80)})` : ''}`
      const next =
        evt.retryable && n < max
          ? ` — re-rolling provider (sort: ${evt.provider_sort || 'default'})…`
          : ' — no attempts left, falling through'
      return this._push(evt.turn, { k: 'retry', t: `Attempt ${n}/${max} ${why}${next}` })
    }

    if (t === 'output_retry') {
      return this._push(evt.turn, {
        k: 'retry',
        t: evt.content
          ? `Output validation failed — retrying: ${evt.content}`
          : 'Output validation failed — retrying.',
      })
    }

    if (t === 'agent_error' || t === 'action_error') {
      return this._push(evt.turn, { k: 'error', t: evt.error || evt.message || JSON.stringify(evt) })
    }

    // screenshot, state_change, button_sequence, turn_trace/compaction_trace/
    // explanation/usage, screen_settling, run_start/end — not part of the live
    // feed (covered by the dedicated events above or by the stats msg).
    return false
  }

  /** Drop every turn (and the compaction rows before it) below `cutoffTurn`. */
  prune(cutoffTurn) {
    if (!(cutoffTurn > 0)) return
    for (const t of [...this.turnBoxes.keys()]) {
      if (t < cutoffTurn) this.turnBoxes.delete(t)
    }
    for (const [t, blocks] of [...this.compactionsByTurn.entries()]) {
      if (t < cutoffTurn) {
        for (const b of blocks) {
          for (const [key, held] of this._byKey.entries()) {
            if (held === b) this._byKey.delete(key)
          }
        }
        this.compactionsByTurn.delete(t)
      }
    }
  }
}
