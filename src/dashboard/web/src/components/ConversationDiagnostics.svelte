<script>
  let { events = [] } = $props()
  const count = (n) => n == null ? 'Not reported' : Number(n).toLocaleString()
  const pct = (n) => n == null ? 'Not reported' : `${(n * 100).toFixed(1)}%`
  const money = (n) => n == null ? 'Not reported' : `$${Number(n).toFixed(6)}`
  const seconds = (n) => n == null ? 'Not reported' : `${Number(n).toFixed(2)} s`
  const words = (s) => s ? s.replaceAll('_', ' ') : 'not reported'
  const perM = (n) => n == null ? 'Not reported' : `$${Number(n).toFixed(3)}/M`
  function billedAt(c) {
    if (!c) return 'No pricing snapshot for this run'
    if (c.status === 'no_pricing_snapshot') return 'No pricing snapshot for this run'
    if (c.status === 'unbilled') return 'Upstream prompt cost not reported'
    if (c.status === 'unpriced') return `${perM(c.billed_prompt_rate_per_m)} · no list price to compare`
    if (c.status === 'rate_above_list') return `${perM(c.billed_prompt_rate_per_m)} · above every list tier`
    return `${perM(c.billed_prompt_rate_per_m)} · list ${perM(c.tier_prompt_per_m)}, cache read ${perM(c.tier_cache_read_per_m)}`
  }
  function impliedDiscount(c) {
    if (!c || c.implied_cached_tokens == null) return 'Cannot be inferred'
    const base = c.implied_cached_tokens === 0
      ? 'None — billed at full list price'
      : `${count(c.implied_cached_tokens)} tokens · ${pct(c.implied_read_fraction)}`
    const agree = {
      matches: 'agrees with the provider report',
      provider_not_reporting: 'provider reports no cache figures',
      billing_implies_more: 'more than the provider reported',
      billing_implies_less: 'less than the provider reported',
    }[c.agreement]
    return agree ? `${base} · ${agree}` : base
  }
  function replay(c) {
    if (c?.reasoning_policy === 'omit_prior') return `Omitted by model policy · ${count(c.archived_blocks)} blocks archived`
    if (c?.local_replay && c.local_replay !== 'intact') return `Check failed: ${words(c.local_replay)}`
    if (c?.expected_blocks === 0) return 'No earlier reasoning blocks'
    if (c?.expected_blocks == null) return 'Not reported'
    return `${count(c.replayed_blocks)} / ${count(c.expected_blocks)} blocks · ${words(c.local_replay)}`
  }
</script>

{#if events.length}
  <details class="diagnostics trace-step">
    <summary><span class="step-label">Technical details</span></summary>
    <div class="diagnostics-body">
      {#each events as event}
        <section class="request-detail">
          {#if event.type === 'llm_request_usage' || event.type === 'llm_request_error'}
            <div class="request-heading">
              <strong>{event.phase === 'compaction' ? 'Compaction request' : 'Gameplay request'}</strong>
              <span>{event.provider || 'Provider not reported'} · attempt {event.attempt ?? '—'}</span>
            </div>
            {#if event.error}<p class="error">{event.error}</p>{/if}
            <div class="metrics">
              <section>
                <h5>Cost &amp; speed</h5>
                <dl>
                  <div><dt>Request cost</dt><dd>{money(event.cost_usd)}</dd></div>
                  <div><dt>Response time</dt><dd>{seconds(event.latency_s)}</dd></div>
                  {#if event.time_to_first_token_s != null}<div><dt>First token</dt><dd>{seconds(event.time_to_first_token_s)}</dd></div>{/if}
                  <div><dt>Input tokens</dt><dd>{count(event.request_tokens)}</dd></div>
                  <div><dt>Output tokens</dt><dd>{count(event.response_tokens)}</dd></div>
                  <div><dt>Of those, reasoning</dt><dd>{count(event.reasoning_tokens)}</dd></div>
                </dl>
              </section>
              <section>
                <h5>Cache reuse</h5>
                <dl>
                  <div><dt>Input from cache</dt><dd>{pct(event.cache_read_fraction)}</dd></div>
                  <div><dt>Tokens read</dt><dd>{count(event.cached_tokens)}</dd></div>
                  <div><dt>Tokens written</dt><dd>{count(event.cache_write_tokens)}</dd></div>
                  {#if event.continuity?.cache_mode}<div><dt>Cache strategy</dt><dd>{words(event.continuity.cache_mode)}</dd></div>{/if}
                  <div><dt>Billed at</dt><dd>{billedAt(event.implied_cache)}</dd></div>
                  <div><dt>Implied discount</dt><dd>{impliedDiscount(event.implied_cache)}</dd></div>
                </dl>
                <p class="hint">Writes prepare for later reuse; reads measure reuse on this request. "Implied by billing" backs the discount out of what the provider charged, so it also works when the provider reports no cache figures.</p>
              </section>
              <section>
                <h5>Conversation continuity</h5>
                <dl>
                  <div><dt>History sent</dt><dd>{event.continuity?.reasoning_policy === 'omit_prior' ? 'Model policy applied' : event.continuity?.history_prefix === 'unchanged' ? 'Unchanged' : words(event.continuity?.history_prefix)}</dd></div>
                  <div><dt>Reasoning sent</dt><dd>{replay(event.continuity)}</dd></div>
                  <div><dt>Request</dt><dd>{event.continuity?.request === 'accepted' ? 'Accepted' : event.type === 'llm_request_error' ? 'Failed' : words(event.continuity?.request)}</dd></div>
                  <div><dt>Provider reasoning feedback</dt><dd>{words(event.continuity?.provider_feedback)}</dd></div>
                  {#if event.continuity?.configured_endpoint}<div><dt>Pinned route</dt><dd>{event.continuity.configured_endpoint}</dd></div>{/if}
                  {#if event.continuity?.profile_name}<div><dt>Profile</dt><dd>{event.continuity.profile_name}</dd></div>{/if}
                  {#if event.continuity?.reasoning_use}<div><dt>Internal reasoning use</dt><dd>{words(event.continuity.reasoning_use)}</dd></div>{/if}
                </dl>
                <p class="hint">Sending earlier reasoning does not confirm the provider used it.</p>
              </section>
            </div>
          {:else if event.type === 'compaction_start'}
            <p class="event-note">Compaction after turn {event.after_turn} · {words(event.reason)}</p>
          {:else if event.type === 'compaction_complete'}
            <p class="event-note">Handover saved after turn {event.after_turn} · segment {event.segment}</p>
          {:else}
            <p class="event-note">{words(event.type)}</p>
          {/if}
          <details class="raw-event">
            <summary>Raw event (JSON)</summary>
            <pre>{JSON.stringify(event, null, 2)}</pre>
          </details>
        </section>
      {/each}
    </div>
  </details>
{/if}

<style>
  .diagnostics { background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: 12.5px; }
  .diagnostics > summary { padding: 7px 10px; display: flex; align-items: center; gap: 8px; list-style: none; }
  .diagnostics > summary::-webkit-details-marker { display: none; }
  .diagnostics > summary::before { content: '▸'; color: var(--faint); font-size: 10px; }
  .diagnostics[open] > summary::before { content: '▾'; }
  .step-label { font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  .diagnostics-body { padding: 0 10px; background: var(--surface); border-top: 1px solid var(--border); }
  .request-detail { padding: 12px 0; }
  .request-detail + .request-detail { border-top: 1px solid var(--border); }
  .request-heading { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; }
  .request-heading span, .event-note { color: var(--muted); font-size: 11.5px; }
  .event-note { margin: 0 0 8px; }
  .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 18px; margin: 14px 0; }
  h5 { margin: 0 0 8px; font-size: 11.5px; }
  dl { margin: 0; font-size: 11.5px; }
  dl > div { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; }
  dt { color: var(--muted); }
  dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
  .hint { color: var(--faint); font-size: 11px; line-height: 1.5; margin: 8px 0 0; }
  .error { color: var(--red); overflow-wrap: anywhere; }
  summary { cursor: pointer; }
  .raw-event { border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
  .raw-event > summary { padding: 7px 10px; color: var(--muted); font-size: 11px; }
  pre { margin: 0; padding: 10px; border-top: 1px solid var(--border); background: var(--surface); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 450px; overflow: auto; font-size: 11px; }
</style>
