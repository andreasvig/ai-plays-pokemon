<script>
  // The small vendor square under a bar and beside a picker row, the same
  // files Artificial Analysis uses (lib/logos.js, Andreas 2026-09-14). A
  // vendor without a file gets its initial in the vendor colour at the same
  // size and radius, so it sits in the row like a mark does.
  import { vendorOf } from '../lib/board.js'
  import { logoUrl } from '../lib/logos.js'
  let { row, size = 16 } = $props()
  const v = $derived(vendorOf(row))
  const src = $derived(logoUrl(v.key))
</script>

{#if src}
  <img class="mark" {src} width={size} height={size} alt={v.label} title={v.label} loading="lazy" />
{:else}
  <span class="mark mono" role="img" aria-label={v.label} title={v.label}
        style={`--c:${v.color}; width:${size}px; height:${size}px; font-size:${Math.round(size * 0.62)}px`}>{v.label[0]}</span>
{/if}

<style>
  .mark { display: inline-flex; align-items: center; justify-content: center; flex: none; vertical-align: middle; border-radius: 3px; }
  img.mark { object-fit: contain; }
  span.mark { background: var(--c); color: #fff; font-weight: 800; line-height: 1; }
</style>
