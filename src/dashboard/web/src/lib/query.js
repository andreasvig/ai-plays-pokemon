// One query string, several owners.
//
// Until 2026-09-28 the model selection owned `location.search` outright: its
// writer rebuilt the URL as `pathname + '?models=…' + hash`, which is fine while
// it is the only parameter and silently deletes every other one the moment it is
// not. The turn-range control is the second owner, so both go through here and
// each one touches only its own key.

/** One parameter's raw value, or null when it is absent. */
export function readParam(search, key) {
  return new URLSearchParams(search || '').get(key)
}

/**
 * `search` with `key` set to `value`, or REMOVED when value is null/''.
 * Returns the query string without the leading '?', so '' means "no query".
 * Parameter order is preserved for the keys already there; a new key is
 * appended, which keeps a link stable when only one control moves.
 */
export function withParam(search, key, value) {
  const qs = new URLSearchParams(search || '')
  if (value == null || value === '') qs.delete(key)
  else qs.set(key, value)
  return qs.toString()
}

/** Write one parameter into the live URL, leaving the others and the hash alone. */
export function setParam(key, value) {
  if (typeof history === 'undefined' || typeof location === 'undefined') return
  const qs = withParam(location.search, key, value)
  history.replaceState(history.state, '', location.pathname + (qs ? '?' + qs : '') + location.hash)
}
