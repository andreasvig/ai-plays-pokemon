<script module>
  // Chunky pixel icons, hand-set on a 12x12 grid (Andreas picked "D chunky",
  // 2026-08-02). Coarser than an off-the-shelf pixel set on purpose: at 12
  // units each "pixel" is twice the size of a 24-grid icon's, which is what
  // makes these read as GBA-era sprites next to the game they wrap rather than
  // as small tidy UI icons.
  //
  // Every icon is a list of [x, y, w, h] rects in grid units, drawn in
  // `currentColor` with shape-rendering=crispEdges — so a parent tints it with
  // `color:` and it stays hard-edged at any size, on paper or on the dark
  // letterbox. Same contract as Action.svelte.
  //
  // Adding one is hand work, which is the known cost of this direction. Draw it
  // on the grid first; a 1px feature (a ring, a thin arrow) turns to mush here
  // — that is why "continue" is a fast-forward rather than a circular arrow.

  /** 4-wide right-pointing wedge, 8 rows tall, anchored top-left. */
  const wedge = (x, y) => [1, 2, 3, 4, 4, 3, 2, 1].map((w, i) => [x, y + i, w, 1])
  /** n-long diagonal of single pixels, stepping by (dx, dy). */
  const diag = (x, y, dx, dy, n) => Array.from({ length: n }, (_, i) => [x + i * dx, y + i * dy, 1, 1])

  // The speaker body + cone, shared by `audio` and `muted`. Kept to the left
  // third of the grid: `muted` needs six clear units to its right to draw an
  // X that survives 17px, and the toggle must not jump between its states.
  const CONE = [[1, 5, 2, 2], [3, 4, 1, 4], [4, 3, 1, 6]]
  // A 12x12 pixel ring — the outline of a circle on this grid. Used by the
  // Poke Ball, and by Action.svelte's face buttons.
  export const RING = [
    [4, 0, 4, 1], [2, 1, 2, 1], [8, 1, 2, 1], [1, 2, 1, 2], [10, 2, 1, 2],
    [0, 4, 1, 4], [11, 4, 1, 4], [1, 8, 1, 2], [10, 8, 1, 2],
    [2, 10, 2, 1], [8, 10, 2, 1], [4, 11, 4, 1],
  ]

  export const ICONS = {
    play: wedge(3, 2),
    // Continue = fast-forward. See the note above on why this isn't a ↻.
    rerun: [...wedge(1, 2), ...wedge(6, 2)],
    // A box with its top-right corner open and an arrow leaving through it.
    report: [
      [1, 4, 5, 1], [1, 4, 1, 7], [1, 10, 8, 1], [8, 6, 1, 5],
      ...diag(5, 6, 1, -1, 5),
      [7, 1, 4, 1], [10, 1, 1, 4],
    ],
    trash: [
      [4, 0, 4, 2], [1, 2, 10, 1], [2, 3, 1, 9], [9, 3, 1, 9], [2, 11, 8, 1],
      [4, 5, 1, 5], [7, 5, 1, 5],
    ],
    close: [...diag(2, 2, 1, 1, 8), ...diag(9, 2, -1, 1, 8)],
    download: [
      [5, 1, 2, 5], [3, 6, 6, 1], [4, 7, 4, 1], [5, 8, 2, 1],
      [1, 10, 1, 1], [10, 10, 1, 1], [1, 11, 10, 1],
    ],
    grip: [[3, 2, 2, 2], [7, 2, 2, 2], [3, 5, 2, 2], [7, 5, 2, 2], [3, 8, 2, 2], [7, 8, 2, 2]],
    back: [
      [6, 1, 2, 1], [5, 2, 2, 1], [4, 3, 2, 1], [3, 4, 2, 1], [2, 5, 2, 1],
      [2, 6, 2, 1], [3, 7, 2, 1], [4, 8, 2, 1], [5, 9, 2, 1], [6, 10, 2, 1],
    ],
    tv: [[1, 1, 10, 1], [1, 1, 1, 7], [10, 1, 1, 7], [1, 7, 10, 1], [5, 8, 2, 2], [2, 10, 8, 1]],
    audio: [...CONE, [6, 4, 1, 4], [8, 3, 1, 6], [10, 2, 1, 8]],
    muted: [...CONE, ...diag(6, 3, 1, 1, 6), ...diag(11, 3, -1, 1, 6)],
    // Poke Ball: ring, an equator broken either side of the button, and the
    // button drawn as a ring of its own so it reads at 20px.
    ball: [
      ...RING,
      [1, 5, 2, 2], [9, 5, 2, 2],
      [5, 4, 2, 1], [4, 5, 1, 2], [7, 5, 1, 2], [5, 7, 2, 1],
    ],
  }
</script>

<script>
  // `size` is in px rather than em: these sit inside fixed-size buttons whose
  // font-size is doing other work (the uppercase micro-labels), so inheriting
  // it would couple two unrelated decisions.
  let { name, size = 22, title = null } = $props()
  const rects = $derived(ICONS[name] ?? [])
</script>

{#if rects.length}
  <svg class="i" viewBox="0 0 12 12" width={size} height={size} fill="currentColor"
       shape-rendering="crispEdges" role={title ? 'img' : 'presentation'}
       aria-label={title} aria-hidden={title ? null : 'true'}>
    {#each rects as [x, y, w, h]}<rect {x} {y} width={w} height={h} />{/each}
  </svg>
{/if}

<style>
  .i { display: block; flex: none; }
</style>
