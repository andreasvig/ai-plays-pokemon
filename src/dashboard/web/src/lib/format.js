export function usd(n) {
  if (n == null) return '—'
  if (n >= 1) return '$' + n.toFixed(2)
  return '$' + n.toFixed(n < 0.1 ? 4 : 3)
}
export function dur(s) {
  if (s == null) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${Math.round(s % 60)}s`
  return `${Math.round(s)}s`
}
export function perTurn(s) {
  if (s == null) return '—'
  return s >= 1 ? `${s.toFixed(1)}s` : `${Math.round(s * 1000)}ms`
}
export function ago(iso) {
  const d = (Date.parse('2026-06-15T00:00:00Z') - Date.parse(iso)) / 86400000
  if (d < 1) return 'today'
  if (d < 2) return 'yesterday'
  return `${Math.round(d)}d ago`
}
export function dateShort(iso) {
  const dt = new Date(iso)
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
// "100%" for a full clear, else "86% · Cascade Badge" with the furthest gate.
export function completionLabel(r) {
  if (r.completion >= 100) return '100%'
  const short = (r.furthestGateName || '').replace(/ \(.*\)$/, '').replace(/^(Reached|Entered|Defeated|Cleared|Stepped outside in|Chose a|Received the|Delivered|Boarded the) /, '')
  return `${r.completion}%`.padStart(3) + (short ? ` · ${short}` : '')
}
