// The ONE model selection every picker-bearing card reads (Andreas 2026-09-14:
// "picking at one would affect the other picker"). Picked aliases live in the
// URL (?models=a,b) so a view can be linked; null means the default, the best
// level per model. The three headline cards ignore it on purpose.
import { applySelection, parsePicked, URL_KEY } from './selection.js'
import { setParam } from './query.js'

const read = () => (typeof location !== 'undefined' ? parsePicked(location.search) : null)

export const selection = (() => {
  let picked = $state(read())
  // Through `setParam`, not by rebuilding the URL: this writer used to replace
  // the whole query string with its own key, which deleted `?turns=` (and any
  // future parameter) the first time anyone touched the model picker.
  // The raw aliases: URLSearchParams does the escaping, and pre-encoding here
  // would land `%2528high%2529` in the bar.
  const write = () => setParam(URL_KEY, picked == null ? null : picked.join(','))
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
