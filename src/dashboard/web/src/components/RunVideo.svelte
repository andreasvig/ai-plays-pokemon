<script>
  // The run's recording, one <video> for every place it plays (History's
  // player, the Report, a model page's expanded level — 2026-09-14). A plain
  // <video controls>: the file is browser-ready H.264, and both the app and R2
  // serve byte ranges, so the scrub bar seeks. A published row carries the R2
  // URL of its recording; a local row streams from the app. `variant` "simple"
  // is the 1:1 recording-simple.mp4 a `both` recording writes beside the full one.
  import { recordingUrl } from '../lib/video.js'
  let { run, variant = 'full', autoplay = false, preload = 'metadata' } = $props()
  const src = $derived(recordingUrl(run, variant))
</script>

<!-- svelte-ignore a11y_media_has_caption -->
<video class="vplayer" {src} controls {autoplay} {preload} playsinline></video>

<style>
  .vplayer { display: block; background: var(--dark); width: 100%; max-height: 78vh; }
</style>
