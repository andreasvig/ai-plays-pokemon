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

// run -> URL slug: <model>-<config>-<timestamp>
export function runSlug(r) {
  const model = r.model.replace('(', '-').replace(')', '')
  const ts = r.startedAt.replace(/[-:T]/g, '').slice(0, 12)
  return `${model}-${r.config}-${ts}`
}
