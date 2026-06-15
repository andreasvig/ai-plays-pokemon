import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Mock-data frontend prototype. Later this build is served by the FastAPI app
// at `/`; for now `npm run dev` serves it standalone for UX iteration.
export default defineConfig({
  plugins: [svelte()],
  server: { port: 5173, host: true },
  build: { outDir: 'dist' },
})
