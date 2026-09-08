import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Mock-data frontend prototype. Later this build is served by the FastAPI app
// at `/`; for now `npm run dev` serves it standalone for UX iteration.
//
// `__STATIC__` is the published-site switch (lib/static.js). A compile-time
// constant rather than a runtime `import.meta.env` read so the bundler drops
// the control-center code paths from the GitHub Pages build; `pokemon publish`
// sets VITE_STATIC=1 for that build and nothing else does.
export default defineConfig({
  plugins: [svelte()],
  define: { __STATIC__: JSON.stringify(process.env.VITE_STATIC === '1') },
  server: { port: 5173, host: true },
  build: { outDir: 'dist' },
})
