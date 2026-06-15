<script>
  import TopBar from './components/TopBar.svelte'
  import Leaderboard from './components/Leaderboard.svelte'
  import Charts from './components/Charts.svelte'
  import History from './components/History.svelte'
  import QueueBar from './components/QueueBar.svelte'
  import Spectate from './components/Spectate.svelte'
  import Report from './components/Report.svelte'
  import AddRunDialog from './components/AddRunDialog.svelte'
  import { router } from './lib/router.svelte.js'
  import * as api from './lib/api.js'

  // route -> view + param
  const parts = $derived(router.path.split('/').filter(Boolean))
  const view = $derived(
    parts[0] === 'spectate' ? 'spectate'
    : parts[0] === 'about' ? 'about'
    : parts[0] === 'history' ? (parts[1] ? 'report' : 'history')
    : 'home'
  )

  let dialogOpen = $state(false)
  let dialogContinueFrom = $state(null)

  // ── live Home data (loaded from /api/* on mount) ──
  let leaderboard = $state([])     // best official run per model (ranked)
  let runs = $state([])            // full history list (History view)
  let queue = $state([])           // upcoming queue items (camelCase cards)
  let activeId = $state(null)      // queue_id of the running run (or null)
  let active = $state(null)        // RunSummary of the running run, joined below
  let emulator = $state({ configured: false, process_up: false, connected: false, busy: false })
  let selectedRun = $state(null)   // resolved run for the report view (P6)
  let models = $state([])          // alias list for the Add-run dialog
  let configs = $state([])         // casual config stems for the dialog

  // spectate pill is green when the emulator is up AND a run is active
  const emulatorUp = $derived(!!(emulator.process_up && emulator.connected))

  // shared leaderboard/chart filters (lifted so both views respect them)
  let priceMax = $state(1)
  let ossFilter = $state('all')
  let maxPrice = $state(1)
  const filteredRows = $derived(
    leaderboard
      .filter((r) => ossFilter === 'all' || r.openSource)
      .filter((r) => r.avgCostPerTurn <= maxPrice + 1e-9)
  )

  // stats chips (mockData exported these precomputed; derive from live rows)
  const stats = $derived({
    modelsRanked: leaderboard.length,
    completers: leaderboard.filter((r) => r.completion >= 100).length,
    totalRuns: runs.length,
    benchmarkVersion: 'pokebench-v1',
  })

  async function loadLeaderboard() {
    const rows = await api.fetchLeaderboard()
    leaderboard = rows
    const max = rows.length ? Math.max(...rows.map((r) => r.avgCostPerTurn)) : 1
    // keep the slider pinned to "show all" unless the user has narrowed it
    const wasAtMax = maxPrice >= priceMax - 1e-9
    priceMax = max
    if (wasAtMax) maxPrice = max
  }
  async function loadRuns() { runs = await api.fetchRuns() }
  async function loadQueue() {
    const { active: a, items } = await api.fetchQueue()
    queue = items
    activeId = a
    // join the active queue_id to its live RunSummary (the running run is in the
    // index but NOT in queue.items). If we can't resolve it, fall back to null.
    if (a) {
      const running = runs.find((r) => r.status === 'running')
        || (await api.fetchRuns({ status: 'running' }).then((rs) => rs[0]).catch(() => null))
      active = running ?? null
    } else {
      active = null
    }
  }
  async function loadEmulator() { emulator = await api.fetchEmulatorStatus() }
  async function loadCatalog() {
    const [m, c] = await Promise.all([
      api.fetchModels().catch(() => []),
      api.fetchConfigs().catch(() => []),
    ])
    models = m; configs = c
  }

  async function loadHome() {
    await Promise.all([
      loadLeaderboard().catch(() => {}),
      loadRuns().catch(() => {}),
      loadEmulator().catch(() => {}),
      loadCatalog(),
    ])
    await loadQueue().catch(() => {})
  }

  // initial load
  $effect(() => { loadHome() })

  // resolve the report view's run on demand (P6 owns the report UI; we just feed it)
  $effect(() => {
    const slug = view === 'report' ? parts[1] : null
    if (!slug) { selectedRun = null; return }
    const cached = runs.find((r) => r.slug === slug) || leaderboard.find((r) => r.slug === slug)
    if (cached) { selectedRun = cached; return }
    api.fetchRun(slug).then((r) => { selectedRun = r }).catch(() => { selectedRun = null })
  })

  const go = (p) => router.navigate(p)
  function inspect(r) { go(`/history/${r.slug}`) }
  function openNew() { dialogContinueFrom = null; dialogOpen = true }
  function openContinue(r) { dialogContinueFrom = r; dialogOpen = true }

  async function submitRun(spec) {
    dialogOpen = false; dialogContinueFrom = null
    try {
      if (spec.continueFrom) {
        await api.continueRun(spec.continueFrom, spec.maxTurns)
      } else {
        await api.enqueueRun(spec)
      }
    } catch (e) { console.error('enqueue failed', e) }
    await loadQueue()
  }
  async function killRun() {
    // ✕ on the active card = stop the running run; the executor auto-advances.
    if (active?.runId) {
      try { await api.stopRun(active.runId) } catch (e) { console.error('stop failed', e) }
    }
    await Promise.all([loadQueue(), loadLeaderboard().catch(() => {}), loadRuns().catch(() => {})])
  }
  async function removeFromQueue(id) {
    queue = queue.filter((q) => q.queueId !== id)   // optimistic
    try { await api.cancelQueued(id) } catch (e) { console.error('cancel failed', e) }
    await loadQueue()
  }
  async function reorder(from, to) {
    const item = queue[from]
    const next = [...queue]
    const [m] = next.splice(from, 1)
    next.splice(to, 0, m)
    queue = next                                     // optimistic
    if (item) {
      try { await api.moveQueued(item.queueId, to) } catch (e) { console.error('move failed', e) }
    }
    await loadQueue()
  }
</script>

<TopBar {active} {emulatorUp} {queue} {view} onnav={go} onspectate={() => go('/spectate')} onnew={openNew} />

<main>
  {#if view === 'home'}
    <QueueBar {active} {queue} onkill={killRun} onremove={removeFromQueue} onreorder={reorder}
      onnew={openNew} onspectate={() => go('/spectate')} />
    <Leaderboard rows={filteredRows} {stats} oninspect={inspect}
      bind:oss={ossFilter} bind:maxPrice={maxPrice} {priceMax} />
    <Charts rows={filteredRows} onpick={(slug) => go(`/history/${slug}`)} />
  {:else if view === 'history'}
    <History {runs} oninspect={inspect} oncontinue={openContinue} />
  {:else if view === 'spectate'}
    <Spectate run={active} onnew={openNew} onback={() => go('/')} />
  {:else if view === 'report'}
    <Report run={selectedRun} onback={() => go('/history')} oncontinue={openContinue} />
  {:else if view === 'about'}
    <section class="about">
      <h2>About PokeBench</h2>
      <p>PokeBench measures whether a language model can <em>play Pokémon FireRed at pace</em>.
        Every official run uses the same harness, the same frozen config, the same ROM, and the
        same starting save — the model is the only variable.</p>
      <p>A <b>deterministic referee</b> reads the game's memory out-of-band (the playing agent never
        sees it) and stamps story checkpoints. A <b>progressive gate ladder</b> attaches a turn
        deadline to each checkpoint; a run that falls behind pace is terminated. A model is scored
        first on how much of the ladder it clears, then — among full clears — on how few turns it took.</p>
      <p class="faint">pokebench-v1 · gate ladder + config are WIP until launch.</p>
    </section>
  {/if}
</main>

<AddRunDialog open={dialogOpen} continueFrom={dialogContinueFrom} {models} {configs}
  onclose={() => { dialogOpen = false; dialogContinueFrom = null }} onsubmit={submitRun} />

<style>
  main { min-height: calc(100vh - 57px); }
  .about { max-width: 680px; margin: 0 auto; padding: 48px 24px; }
  .about h2 { font-size: 26px; font-weight: 780; letter-spacing: -.02em; margin: 0 0 16px; }
  .about p { font-size: 15px; line-height: 1.65; color: var(--muted); }
  .about em { font-style: italic; color: var(--text); }
  .about b { color: var(--text); }
</style>
