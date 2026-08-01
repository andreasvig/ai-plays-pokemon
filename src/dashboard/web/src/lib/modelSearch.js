// Fuzzy matching for the model picker.
//
// Two rules pull in opposite directions and both matter:
//
//   LETTERS are forgiving. Typos and missing separators should still find the
//   model — "gbt 5.1", "gpt5.1" and "gpt-5.1" are all the same intent.
//
//   DIGITS are strict. A model's version IS its identity: "gpt-5.1" and
//   "gpt-4.1" are different models, and a search that blurs them is worse than
//   one that finds nothing. So digits never fuzzy-match.
//
// The trick that satisfies both is to strip separators from the digits rather
// than tolerate wrong ones: every digit in the query, in order, must be a
// PREFIX of every digit in the candidate, in order. Dots and dashes vanish, the
// values do not.
//
//   "5.1" -> "51"  vs  "gpt-5.1" -> "51"   prefix, match
//   "5.1" -> "51"  vs  "gpt-4.1" -> "41"   no
//   "51"  -> "51"  vs  "gpt-5.1" -> "51"   match (missing dot costs nothing)
//   "5"   -> "5"   vs  "gpt-5.5" -> "55"   prefix, match (the 5.x family)
//   "1"   -> "1"   vs  "gemini-3.1" -> "31" no (not a prefix — "1" is not "3")

const LETTER_RUN = /[a-z]+/g
const DIGIT = /\d/g

function norm(s) {
  return String(s ?? '')
    .toLowerCase()
    .trim()
}

/** Every digit in order, separators discarded. `"gpt-5.1"` -> `"51"`. */
export function digitsOf(s) {
  return (norm(s).match(DIGIT) || []).join('')
}

/** Letter runs, separators discarded. `"gpt-5.1-pro"` -> `["gpt", "pro"]`. */
export function lettersOf(s) {
  return norm(s).match(LETTER_RUN) || []
}

/**
 * Levenshtein distance, abandoned once it exceeds `max` (returns `max + 1`).
 * Bailing early keeps this cheap enough to run over the whole catalogue on
 * every keystroke.
 */
export function editDistance(a, b, max) {
  if (a === b) return 0
  if (Math.abs(a.length - b.length) > max) return max + 1
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i)
  for (let i = 1; i <= a.length; i++) {
    const cur = [i]
    let best = i
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(
        prev[j] + 1,
        cur[j - 1] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)
      )
      if (cur[j] < best) best = cur[j]
    }
    if (best > max) return max + 1
    prev = cur
  }
  return prev[b.length]
}

// A longer word can absorb more typing damage before it stops being the word
// you meant; "a" vs "b" at distance 1 is a different word entirely.
function typoBudget(len) {
  // One or two letters carry no redundancy — at distance 1 they are simply a
  // different word, so "k" would "correct" to the "v" in mimo-v2.5. Exact or
  // prefix only below three characters.
  if (len <= 2) return 0
  if (len <= 6) return 1
  return 2
}

/**
 * How well one query letter-run matches one candidate letter-run.
 * `null` when it doesn't; otherwise a score where exact beats prefix beats typo.
 */
function scoreLetterToken(q, c) {
  if (q === c) return 30
  if (c.startsWith(q)) return q.length >= 2 ? 22 : 12
  const d = editDistance(q, c, typoBudget(q.length))
  if (d <= typoBudget(q.length)) return 14 - 3 * d
  // A typo in a prefix — "gbt" against "gpt-oss" style names, where the
  // candidate run is longer than what was typed.
  if (c.length > q.length) {
    const dp = editDistance(q, c.slice(0, q.length), typoBudget(q.length))
    if (dp <= typoBudget(q.length)) return 10 - 3 * dp
  }
  return null
}

/**
 * Score `name` against `query`. Higher is better; `null` means no match.
 *
 * Every letter run in the query must find a home in the candidate, and the
 * query's digits must prefix the candidate's. Both conditions, not either.
 */
export function scoreModel(query, name) {
  const q = norm(query)
  if (!q) return 0
  const n = norm(name)

  // Digits first — it's the cheap veto, and the one that must never be fudged.
  const qd = digitsOf(q)
  const cd = digitsOf(n)
  if (qd && !cd.startsWith(qd)) return null

  const qLetters = lettersOf(q)
  const cLetters = lettersOf(n)
  let score = 0

  for (const qt of qLetters) {
    let best = null
    for (const ct of cLetters) {
      const s = scoreLetterToken(qt, ct)
      if (s != null && (best == null || s > best)) best = s
    }
    // Also try the candidate's letters joined, so "gptoss" finds "gpt-oss".
    if (cLetters.length > 1) {
      const s = scoreLetterToken(qt, cLetters.join(''))
      if (s != null && (best == null || s > best)) best = s
    }
    if (best == null) return null
    score += best
  }

  if (qd) score += qd === cd ? 40 : 20

  // A straight substring of the raw name is almost certainly what was meant.
  if (n.includes(q)) score += 60
  // Separator-insensitive substring: "gpt51" against "gpt-5.1".
  if (n.replace(/[^a-z0-9]/g, '').includes(q.replace(/[^a-z0-9]/g, ''))) score += 25
  if (n.startsWith(q)) score += 15

  // Among equally good matches prefer the tighter name, so "gpt-5.5" outranks
  // "gpt-5.5-pro" for the query "gpt-5.5".
  score -= Math.min(10, n.length / 6)

  return score
}

/**
 * Filter + rank `models` (rows with a `.model` name) against `query`.
 * `tieBreak(a, b)` orders equal scores and is also the order returned when the
 * query is empty — the picker passes its release-date comparator.
 */
export function searchModels(models, query, tieBreak) {
  const cmp = tieBreak || (() => 0)
  if (!norm(query)) return [...models].sort(cmp)
  return models
    .map((m) => ({ m, s: scoreModel(query, m.model) }))
    .filter((r) => r.s != null)
    .sort((a, b) => b.s - a.s || cmp(a.m, b.m))
    .map((r) => r.m)
}
