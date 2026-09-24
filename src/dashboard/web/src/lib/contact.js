// Where to reach Andreas, and where the source lives. One file so the Contact
// page, the Methodology header and anything added later cannot disagree about a
// URL (Andreas 2026-09-16).
//
// A channel with no URL yet is simply LEFT OUT rather than shipped as a dead
// link — the page renders whatever is in LINKS.

/** The repo the board is built from. Public; the Methodology page links it too. */
export const REPO_URL = 'https://github.com/andreasvig/ai-plays-pokemon'

/** Public contact address. Empty string hides the row. */
export const CONTACT_EMAIL = 'skotte.andreas@gmail.com'

export const LINKS = [
  { key: 'github', label: 'GitHub', href: REPO_URL, text: 'andreasvig/ai-plays-pokemon' },
  { key: 'linkedin', label: 'LinkedIn', href: 'https://www.linkedin.com/in/andreas-vig-astrup-b686532b5/', text: 'Andreas Vig Astrup' },
  { key: 'youtube', label: 'YouTube', href: 'https://www.youtube.com/@andreasastrup3425', text: '@andreasastrup3425' },
]
