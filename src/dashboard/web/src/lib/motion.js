// A seconds clock for the route map's marching chevrons.
//
// One place, because both surfaces that draw a route animate it — the world map
// and the interior popup — and a second copy of this is a second chance to
// leave a requestAnimationFrame running on a page nobody is looking at.
//
// Three things it is responsible for:
//  - `prefers-reduced-motion`: the viewer gets ONE still frame at t = 0 and no
//    loop at all, rather than a slowed-down one;
//  - visibility: the map sits partway down a long page, so the loop only runs
//    while the canvas is actually on screen;
//  - a `tick(0)` before the loop starts, so the arrows are on the very first
//    paint rather than one frame late.

/** True when the viewer has asked their system for less movement. */
export const reducedMotion = () =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

/**
 * Call `tick(seconds)` every frame while `el` is on screen.
 * Returns a stop function; call it on teardown.
 */
export function motionClock(el, tick) {
  tick(0)
  if (reducedMotion() || typeof requestAnimationFrame !== 'function') return () => {}

  let raf = 0
  let stopped = false
  const loop = (ms) => {
    if (stopped) return
    tick(ms / 1000)
    raf = requestAnimationFrame(loop)
  }
  const start = () => { if (!raf && !stopped) raf = requestAnimationFrame(loop) }
  const halt = () => { if (raf) cancelAnimationFrame(raf); raf = 0 }

  let io = null
  if (el && typeof IntersectionObserver === 'function') {
    io = new IntersectionObserver(([e]) => (e.isIntersecting ? start() : halt()), { rootMargin: '150px' })
    io.observe(el)
  } else {
    start()
  }
  return () => { stopped = true; halt(); io?.disconnect() }
}
