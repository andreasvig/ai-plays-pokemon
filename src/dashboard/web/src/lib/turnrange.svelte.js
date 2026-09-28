// The live per-level turn range, backed by `?turns=` so a slice can be linked.
// Pure parsing lives in turnrange.js; this is the runes store the components read.
import { parseRanges, serializeRanges, URL_KEY } from './turnrange.js'
import { setParam } from './query.js'

const read = () => (typeof location !== 'undefined' ? parseRanges(location.search) : {})

export const turnRanges = (() => {
  let ranges = $state(read())
  const write = () => setParam(URL_KEY, serializeRanges(ranges))
  if (typeof window !== 'undefined') window.addEventListener('popstate', () => { ranges = read() })
  return {
    get all() { return ranges },
    /** The range for one level, or null for "the whole run". */
    get(level) { return ranges[level] ?? null },
    set(level, range) {
      const next = { ...ranges }
      if (range == null) delete next[level]
      else next[level] = [range[0], range[1]]
      ranges = next
      write()
    },
  }
})()
