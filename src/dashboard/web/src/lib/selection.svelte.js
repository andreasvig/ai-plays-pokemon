// The ONE model selection every picker-bearing card reads (Andreas 2026-09-14:
// "picking at one would affect the other picker"). Picked aliases live in the
// URL (?models=a,b) so a view can be linked; null means the default, the best
// level per model. The three headline cards ignore it on purpose.
import { applySelection, parsePicked, serializePicked } from './selection.js'

const read = () => (typeof location !== 'undefined' ? parsePicked(location.search) : null)

export const selection = (() => {
  let picked = $state(read())
  const write = () => {
    if (typeof history === 'undefined') return
    const qs = serializePicked(picked)
    history.replaceState(history.state, '', location.pathname + (qs ? '?' + qs : '') + location.hash)
  }
  if (typeof window !== 'undefined') window.addEventListener('popstate', () => { picked = read() })
  return {
    get picked() { return picked },
    set(list) { picked = list == null ? null : [...list]; write() },
    toggle(alias, rows) {
      const cur = new Set(applySelection(rows, picked).map((r) => r.model))
      if (cur.has(alias)) cur.delete(alias); else cur.add(alias)
      this.set(rows.filter((r) => cur.has(r.model)).map((r) => r.model))
    },
    apply(rows) { return applySelection(rows, picked) },
  }
})()
