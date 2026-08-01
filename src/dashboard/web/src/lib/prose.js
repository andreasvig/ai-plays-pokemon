// Paragraph flow for model reasoning.
//
// The non-obvious part of the simple view (build plan §6). A turn's reasoning
// arrives with ~18 hard newlines in it — the model wraps its own prose and
// numbers its plan steps on separate lines. Rendering that with
// `white-space: pre-wrap` honours every one of those newlines, so 946
// characters demanded 195px of height in a box that had 129px. It looks like a
// box-sizing bug and is not one.
//
// Rule: a BLANK line is a paragraph break; a LONE newline is the model's own
// wrapping, carries no meaning, and becomes a space. Same words, ~5 flowing
// lines instead of 18 rigid ones — which is what lets the box be 24% of the
// frame without truncating. Do not restore `pre-wrap` on the reasoning.

/**
 * Split reasoning prose into paragraphs, collapsing the model's soft wrapping.
 * @param {unknown} txt raw reasoning
 * @returns {string[]} paragraphs, each a single flowing line, never empty
 */
export function splitParas(txt) {
  return String(txt ?? '')
    .trim()
    .split(/\n\s*\n/)
    .map((p) => p.replace(/\s*\n\s*/g, ' ').trim())
    .filter(Boolean)
}

/**
 * Paragraphs → per-paragraph word arrays, for the word-by-word stream.
 * Split on runs of whitespace so a double space never emits an empty "word"
 * (which would stall the stream for a tick while adding nothing visible).
 * @param {string[]} paras
 * @returns {string[][]}
 */
export function paraWords(paras) {
  return paras.map((p) => p.split(/\s+/).filter(Boolean))
}

/**
 * Words-per-tick so a stream of `total` words finishes in ~`totalMs` at a
 * `tickMs` cadence, independent of how long the reasoning is. At least 1, so
 * the stream always advances.
 */
export function streamStep(total, totalMs, tickMs) {
  return Math.max(1, Math.ceil((total || 1) / Math.max(1, totalMs / tickMs)))
}

/**
 * The visible paragraph list part-way through the stream: every completed
 * paragraph in full, plus the in-progress one truncated at `wi` words.
 * @param {string[][]} words
 * @param {number} pi index of the in-progress paragraph
 * @param {number} wi words revealed of it
 */
export function streamSlice(words, pi, wi) {
  const done = words.slice(0, pi).map((w) => w.join(' '))
  if (pi < words.length) done.push(words[pi].slice(0, wi).join(' '))
  return done
}
