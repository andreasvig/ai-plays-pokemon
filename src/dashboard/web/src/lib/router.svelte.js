// Minimal path-based router (History API). Routes:
//   /                          home (leaderboard + charts)
//   /spectate                  live run + queue
//   /history                   run list
//   /history/<slug>            run detail (report)
//   /about                     about
export const router = (() => {
  let path = $state(typeof location !== 'undefined' ? location.pathname : '/')
  if (typeof window !== 'undefined') {
    window.addEventListener('popstate', () => { path = location.pathname })
  }
  return {
    get path() { return path },
    navigate(to) {
      if (to !== path) { history.pushState({}, '', to); path = to; window.scrollTo(0, 0) }
    },
  }
})()

// run -> URL slug. Wiring decision ("Run identity", go): the canonical slug is
// the real on-disk run-dir name (`run_id`), so /history/<id> and /api/runs/<id>
// share one identifier. Falls back to a synthesized slug only if runId is absent
// (defensive — real + mock rows both carry runId).
export function runSlug(r) {
  if (r.runId) return r.runId
  const model = r.model.replace('(', '-').replace(')', '')
  const ts = (r.startedAt || '').replace(/[-:T]/g, '').slice(0, 12)
  return `${model}-${r.config}-${ts}`
}
