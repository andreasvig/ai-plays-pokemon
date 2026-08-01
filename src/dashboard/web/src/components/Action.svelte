<script module>
  // Split an action value into GBA button tokens. Accepts an array, a
  // space-joined string ("up up a"), or a bracketed one ("[up, up, a]") —
  // all three shapes reach the UI depending on which endpoint produced them.
  export function actionTokens(action) {
    if (action == null) return []
    const list = Array.isArray(action)
      ? action.map((t) => String(t))
      : String(action).replace(/[[\]]/g, '').split(/[,\s]+/)
    return list.map((t) => t.trim()).filter(Boolean)
  }
</script>

<script>
  // One GBA control glyph as inline SVG. Draws in `currentColor` and sizes in
  // `em`, so a parent tints and scales it with a single rule — that is what
  // lets the same component serve the paper-themed simple view and the dark
  // trace feed. Paths are ported verbatim from docs/simple-view-mock.html;
  // the 2.35em height is load-bearing, not a default: at 1.8em the d-pad
  // direction was not readable at a glance, which defeats the point of using
  // real hardware icons instead of emoji.
  //
  // `delay` (ms) opts into the staggered pop the simple view uses for its
  // fake streaming. Leave it null everywhere else and the glyph is static.
  let { token, delay = null } = $props()

  const DIR = {
    up: 'M12 5.4 15.4 10.2 8.6 10.2Z',
    down: 'M12 18.6 15.4 13.8 8.6 13.8Z',
    left: 'M5.4 12 10.2 8.6 10.2 15.4Z',
    right: 'M18.6 12 13.8 8.6 13.8 15.4Z',
  }
  const CROSS = 'M9.1 2.6h5.8v6.5h6.5v5.8h-6.5v6.5H9.1v-6.5H2.6V9.1h6.5z'

  let k = $derived(String(token ?? '').toLowerCase())
  let dir = $derived(DIR[k] ?? null)
  let face = $derived(k === 'a' || k === 'b')
  let pill = $derived(k === 'start' || k === 'select')
  let style = $derived(delay == null ? null : `animation-delay:${delay}ms`)
</script>

{#if dir}
  <svg class="g" class:pop={delay != null} {style} viewBox="0 0 24 24"
       width="2.35em" height="2.35em" role="img" aria-label={k}>
    <path d={CROSS} fill="none" stroke="currentColor" stroke-width="1.3" opacity=".45" />
    <path d={dir} fill="currentColor" />
  </svg>
{:else if face}
  <svg class="g" class:pop={delay != null} {style} viewBox="0 0 24 24"
       width="2.35em" height="2.35em" role="img" aria-label={k}>
    <circle cx="12" cy="12" r="8.6" fill="none" stroke="currentColor" stroke-width="1.3" opacity=".55" />
    <text x="12" y="12" fill="currentColor" font-size="9.4" font-weight="700"
          font-family="ui-monospace,monospace" text-anchor="middle"
          dominant-baseline="central">{k.toUpperCase()}</text>
  </svg>
{:else if pill}
  <svg class="g" class:pop={delay != null} {style} viewBox="0 0 34 24"
       width="3.33em" height="2.35em" role="img" aria-label={k}>
    <rect x="2.4" y="8.6" width="29" height="6.6" rx="3.3" fill="none" stroke="currentColor"
          stroke-width="1.3" opacity=".55" transform="rotate(-16 17 12)" />
    <text x="17" y="12.4" fill="currentColor" font-size="6.4" font-weight="700"
          font-family="ui-monospace,monospace" text-anchor="middle"
          dominant-baseline="central" letter-spacing=".6">{k.toUpperCase()}</text>
  </svg>
{:else if k === 'wait'}
  <svg class="g" class:pop={delay != null} {style} viewBox="0 0 24 24"
       width="2.35em" height="2.35em" role="img" aria-label="wait">
    <circle cx="12" cy="12" r="8.6" fill="none" stroke="currentColor" stroke-width="1.3" opacity=".55" />
    <path d="M12 7.2V12l3.2 2.1" fill="none" stroke="currentColor" stroke-width="1.5"
          stroke-linecap="round" />
  </svg>
{:else}
  <!-- Unknown token: render it as text rather than dropping it, so a button
       the registry gains tomorrow degrades to a label instead of vanishing. -->
  <span class="g txt" class:pop={delay != null} {style}>{token}</span>
{/if}

<style>
  .g { flex: none; vertical-align: middle; }
  .txt { font-weight: 600; font-size: .95em; }
  .pop { animation: pop 260ms cubic-bezier(.2, 1.4, .4, 1) both; }
  @keyframes pop {
    from { opacity: 0; transform: scale(.7); }
    to   { opacity: 1; transform: scale(1); }
  }
  @media (prefers-reduced-motion: reduce) {
    .pop { animation-duration: 1ms; }
  }
</style>
