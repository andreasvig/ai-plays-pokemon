<script>
  // The small vendor square under a bar and beside a picker row (Artificial
  // Analysis style, Andreas 2026-09-14). A real mark where simple-icons has one
  // (lib/logos.js), otherwise the vendor's initial in its colour — same size,
  // same corner radius, so a monogram sits in the row like a mark does.
  import { vendorOf } from '../lib/board.js'
  import { MARKS } from '../lib/logos.js'
  let { row, size = 16 } = $props()
  const v = $derived(vendorOf(row))
  const mark = $derived(MARKS[v.key] ?? null)
</script>

{#if mark}
  <svg class="mark" width={size} height={size} viewBox="0 0 24 24" role="img" aria-label={v.label} style={`--c:${v.color}`}>
    <title>{v.label}</title>
    <path d={mark.path} fill="var(--c)" />
  </svg>
{:else}
  <span class="mark mono" role="img" aria-label={v.label} title={v.label}
        style={`--c:${v.color}; width:${size}px; height:${size}px; font-size:${Math.round(size * 0.62)}px`}>{v.label[0]}</span>
{/if}

<style>
  .mark { display: inline-flex; align-items: center; justify-content: center; flex: none; vertical-align: middle; }
  span.mark { background: var(--c); color: #fff; border-radius: 3px; font-weight: 800; line-height: 1; }
</style>
