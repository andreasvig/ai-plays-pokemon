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
  // One GBA control glyph, drawn as chunky pixels on the same 12x12 grid as
  // Icon.svelte (Andreas, 2026-08-02 — "the up/down/left/right logos similar
  // also"). Draws in `currentColor` and sizes in `em`, so a parent tints and
  // scales it with a single rule — that is what lets the same component serve
  // the paper-themed simple view, the trace feed and the run report.
  //
  // The 2.35em height is load-bearing, not a default: at 1.8em the d-pad
  // direction was not readable at a glance, which defeats the point of using
  // real hardware icons instead of emoji.
  //
  // One thing the pixel rewrite gives up: start/select used to be a pill
  // rotated -16deg, a nice nod to the hardware. Rotation destroys crisp edges,
  // so the pill is now square-on — and slightly longer, because the label has
  // to fit a 12-unit-tall box instead of a 24-unit one.
  //
  // `delay` (ms) opts into the staggered pop the simple view uses for its
  // fake streaming. Leave it null everywhere else and the glyph is static.
  import { RING } from './Icon.svelte'

  let { token, delay = null } = $props()

  // The d-pad body as a 1px OUTLINE, drawn faint. Filled bars were the first
  // attempt and they cross: the shared centre composited its opacity twice and
  // the glyph read as a mottled grey plus.
  const CROSS = [
    [4, 0, 4, 1], [4, 0, 1, 4], [7, 0, 1, 4],
    [0, 4, 4, 1], [0, 4, 1, 4], [0, 7, 4, 1],
    [4, 8, 1, 4], [4, 11, 4, 1], [7, 8, 1, 4],
    [8, 7, 4, 1], [11, 4, 1, 4], [8, 4, 4, 1],
  ]
  // The pressed arm, filled solid. At 17px a filled quadrant reads as the
  // direction instantly; a three-row arrowhead inside the arm did not.
  // One unit longer than the arm, so the fill plugs the mouth the outline
  // leaves open and the two read as one shape rather than a block floating
  // beside a cross.
  const ARROW = {
    up:    [[4, 0, 4, 5]],
    down:  [[4, 7, 4, 5]],
    left:  [[0, 4, 5, 4]],
    right: [[7, 4, 5, 4]],
  }
  // Square-on pill for start/select, on a 22x12 grid so six characters fit.
  const PILL = [[3, 4, 16, 1], [3, 7, 16, 1], [2, 5, 1, 2], [19, 5, 1, 2]]
  // Clock hands for `wait`, meeting at the ring's centre pixel.
  const HANDS = [[5, 3, 1, 3], [6, 5, 3, 1]]

  let k = $derived(String(token ?? '').toLowerCase())
  let dir = $derived(ARROW[k] ?? null)
  let face = $derived(k === 'a' || k === 'b')
  let pill = $derived(k === 'start' || k === 'select')
  let style = $derived(delay == null ? null : `animation-delay:${delay}ms`)
</script>

{#snippet px(rects, opacity)}
  <g {opacity}>{#each rects as [x, y, w, h]}<rect {x} {y} width={w} height={h} />{/each}</g>
{/snippet}

{#if dir}
  <svg class="g" class:pop={delay != null} {style} viewBox="0 0 12 12"
       width="2.35em" height="2.35em" fill="currentColor" shape-rendering="crispEdges"
       role="img" aria-label={k}>
    {@render px(CROSS, .38)}
    {@render px(dir, 1)}
  </svg>
{:else if face}
  <svg class="g" class:pop={delay != null} {style} viewBox="0 0 12 12"
       width="2.35em" height="2.35em" fill="currentColor" shape-rendering="crispEdges"
       role="img" aria-label={k}>
    {@render px(RING, .55)}
    <text x="6" y="6.4" fill="currentColor" font-size="6.6" font-weight="700"
          font-family="ui-monospace,monospace" text-anchor="middle"
          dominant-baseline="central">{k.toUpperCase()}</text>
  </svg>
{:else if pill}
  <svg class="g" class:pop={delay != null} {style} viewBox="0 0 22 12"
       width="4.31em" height="2.35em" fill="currentColor" shape-rendering="crispEdges"
       role="img" aria-label={k}>
    {@render px(PILL, .55)}
    <text x="11" y="6.2" fill="currentColor" font-size="4.6" font-weight="700"
          font-family="ui-monospace,monospace" text-anchor="middle"
          dominant-baseline="central" letter-spacing=".3">{k.toUpperCase()}</text>
  </svg>
{:else if k === 'wait'}
  <svg class="g" class:pop={delay != null} {style} viewBox="0 0 12 12"
       width="2.35em" height="2.35em" fill="currentColor" shape-rendering="crispEdges"
       role="img" aria-label="wait">
    {@render px(RING, .55)}
    {@render px(HANDS, 1)}
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
