// Tiny, XSS-safe markdown→HTML renderer for the LIVE trace (thinking boxes).
// Deliberately minimal: escape ALL HTML first (so nothing the model emits can
// inject markup), THEN apply a handful of inline + block transforms. No deps.
//
// Supported:
//   **bold**         → <strong>bold</strong>   (used as section headers)
//   *em* / _em_      → <em>em</em>
//   blank-line gaps  → separate <p>…</p> paragraphs
//   single newlines  → <br> within a paragraph

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function inline(s) {
  // Escaping already ran, so these regexes only ever see entity-safe text.
  return s
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[^_])_([^_\n]+)_/g, '$1<em>$2</em>')
}

export function mdToHtml(src) {
  if (src == null) return ''
  const escaped = escapeHtml(src)
  // Split into paragraphs on blank lines; within a paragraph, single newlines
  // become <br>. Drop empty paragraphs.
  return escaped
    .split(/\n{2,}/)
    .map((para) => para.trim())
    .filter((para) => para.length)
    .map((para) => '<p>' + inline(para).replace(/\n/g, '<br>') + '</p>')
    .join('')
}
