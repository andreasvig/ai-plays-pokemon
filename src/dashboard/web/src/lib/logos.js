// Vendor marks under the bars (VendorMark.svelte): the small brand files
// Artificial Analysis serves at /img/logos/, copied 2026-09-14 into
// public/logos/ (Andreas: "get the small images from Artificial Analysis
// themselves"). Keys are the vendor keys of lib/board.js VENDORS; a vendor
// missing here (Sakana) gets a letter monogram. Vite copies public/ to the
// bundle root, so the URL is BASE_URL + 'logos/<file>'.
export const LOGO_FILES = {
  openai: 'openai.svg',
  google: 'google.svg',
  anthropic: 'anthropic.svg',
  deepseek: 'deepseek.svg',
  meta: 'meta.svg',
  'x-ai': 'x-ai.svg',
  'z-ai': 'z-ai.svg',
  alibaba: 'alibaba.svg',
  minimax: 'minimax.svg',
  xiaomi: 'xiaomi.svg',
  moonshotai: 'moonshotai.jpg',
}

const BASE = (import.meta.env?.BASE_URL || '/')
export function logoUrl(vendorKey) {
  const f = LOGO_FILES[vendorKey]
  return f ? `${BASE}logos/${f}` : null
}
