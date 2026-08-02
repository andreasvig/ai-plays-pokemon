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
  import { recording, recordRun, forcedSimple } from './lib/record.js'
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
  let emulator = $state({ configured: false, process_up: false, connected: false, busy: false, active_run_id: null, muted: true, rom: null, switching_to: null, awaiting_lua: false })
  let selectedRun = $state(null)   // resolved run for the report view (P6)

  // active run id for spectate streams (Plan §P6) — the run-dir id the executor
  // is currently driving, from /api/emulator/status; null in headless/between runs.
  // A recorder page pins its run id from the URL (see lib/record.js): the
  // emulator's active_run_id goes null the moment the run ends, and a recorder
  // that followed it would cut to the "waiting for a live run" panel for the
  // last seconds of every video.
  const activeRunId = $derived(recordRun ?? emulator.active_run_id ?? null)
  let models = $state([])          // alias list for the Add-run dialog
  let configs = $state([])         // casual config stems for the dialog
  let benchmarks = $state([])      // benchmark registry [{id,name,goal,...}]
  let checkpoints = $state([])     // full ladder [{id,name,type}] — casual "Stop at"
  let roms = $state([])            // game registry [{id,name,benchmark_ok,on_disk}]
  let benchmark = $state('')       // selected benchmark id (scopes the leaderboard)

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

  // name of the currently-selected benchmark (shown as the leaderboard chip)
  const benchmarkName = $derived(benchmarks.find((b) => b.id === benchmark)?.name ?? benchmark)

  // stats chips (mockData exported these precomputed; derive from live rows)
  const stats = $derived({
    modelsRanked: leaderboard.length,
    completers: leaderboard.filter((r) => r.completion >= 100).length,
    totalRuns: runs.length,
    benchmarkVersion: benchmarkName,
  })

  async function loadLeaderboard() {
    const rows = await api.fetchLeaderboard(benchmark || null)
    leaderboard = rows
    const max = rows.length ? Math.max(...rows.map((r) => r.avgCostPerTurn)) : 1
    // keep the slider pinned to "show all" unless the user has narrowed it
    const wasAtMax = maxPrice >= priceMax - 1e-9
    priceMax = max
    if (wasAtMax) maxPrice = max
  }
  async function loadRuns() { runs = await api.fetchRuns() }

  // History delete (typed-DELETE confirmation lives in History.svelte). Trashes
  // the run server-side, then refreshes the slices it can affect.
  async function removeRun(run) {
    await api.deleteRun(run.runId)
    await Promise.all([loadRuns().catch(() => {}), loadLeaderboard().catch(() => {})])
  }
  async function loadQueue() {
    const { active: a, items } = await api.fetchQueue()
    activeId = a
    // The real /api/queue returns {active: <queue_id>, items: [ALL items incl.
    // the active one]} (Plan §Round 7). Split it: the active queue item is the
    // running run; the rest are the draggable upcoming cards. Without this the
    // running run shows as an upcoming card and the strip's active slot is empty.
    const activeItem = a ? items.find((q) => q.queueId === a) : null
    queue = items.filter((q) => q.queueId !== a)
    // Resolve the active run's live RunSummary so the active card + spectate bar
    // show real model/kind/turn. Prefer the executor's active_run_id from
    // emulator status (authoritative for spectate streams); fall back to a
    // running-status scan. If the index hasn't caught up yet, fall back to the
    // active queue item itself (kind/model are enough to populate the card +
    // flip the TopBar pill green).
    const liveId = emulator.active_run_id ?? null
    let resolved = null
    if (liveId) {
      resolved = runs.find((r) => r.runId === liveId)
        || (await api.fetchRun(liveId).catch(() => null))
    } else if (a) {
      resolved = runs.find((r) => r.status === 'running')
        || (await api.fetchRuns({ status: 'running' }).then((rs) => rs[0]).catch(() => null))
    }
    // When there IS an active queue item, never leave `active` null — fall back
    // to the queue item so the active card renders even before the RunSummary
    // is available. When there's no active item, clear it.
    active = resolved ?? (activeItem ? { ...activeItem, runId: liveId } : null)
  }
  async function loadEmulator() { emulator = await api.fetchEmulatorStatus() }
  async function loadCatalog() {
    const [m, c, b, k, r] = await Promise.all([
      api.fetchModels().catch(() => []),
      api.fetchConfigs().catch(() => []),
      api.fetchBenchmarks().catch(() => []),
      api.fetchCheckpoints().catch(() => []),
      api.fetchRoms().catch(() => []),
    ])
    models = m; configs = c; benchmarks = b; checkpoints = k; roms = r
    // Default the leaderboard filter to the registry-default benchmark (or the
    // first) once, without clobbering a selection the user already made.
    if (!benchmark && b.length) benchmark = (b.find((x) => x.default) ?? b[0]).id
  }

  // User picked a different benchmark on the leaderboard → re-scope it.
  async function selectBenchmark(id) {
    benchmark = id
    await loadLeaderboard().catch(() => {})
  }

  async function loadHome() {
    // Catalog first: it sets the default `benchmark`, which loadLeaderboard reads
    // to scope the board. (Without this ordering the first board fetch would be
    // unscoped — showing all benchmarks until the next refresh.)
    await loadCatalog().catch(() => {})
    await Promise.all([
      loadLeaderboard().catch(() => {}),
      loadRuns().catch(() => {}),
      loadEmulator().catch(() => {}),
    ])
    await loadQueue().catch(() => {})
  }

  // initial load
  $effect(() => { loadHome() })

  // ── live-live home updates via /api/ws/control (locked #7, NO polling) ──
  // The server pushes a small blob on every state change (a run starting /
  // finishing, the next item auto-dequeuing, a queue edit, a new leaderboard
  // row). We refetch the affected slices on each ping. The user's own queue
  // edits still update optimistically below; this catches the async changes.
  $effect(() => {
    const sock = api.openControlSocket(() => {
      loadEmulator().catch(() => {})       // active_run_id may have changed
      loadQueue().catch(() => {})          // active + items
      loadLeaderboard().catch(() => {})    // a finished run may add a row
      loadRuns().catch(() => {})           // history list
    })
    return () => sock.close()
  })

  // resolve the report view's run on demand (P6 owns the report UI; we just feed it)
  $effect(() => {
    const slug = view === 'report' ? parts[1] : null
    if (!slug) { selectedRun = null; return }
    const cached = runs.find((r) => r.slug === slug) || leaderboard.find((r) => r.slug === slug)
    if (cached) { selectedRun = cached; return }
    api.fetchRun(slug).then((r) => { selectedRun = r }).catch(() => { selectedRun = null })
  })

  // Round 11: /spectate is a fit-to-screen kiosk view. Lock page scroll while on
  // the spectate route (the frame owns the whole window); clean up on leave.
  $effect(() => {
    const lock = view === 'spectate'
    document.body.classList.toggle('spectate-lock', lock)
    return () => document.body.classList.remove('spectate-lock')
  })

  const go = (p) => router.navigate(p)
  function inspect(r) { go(`/history/${r.slug}`) }
  function openNew() { dialogContinueFrom = null; dialogOpen = true }
  function openContinue(r) { dialogContinueFrom = r; dialogOpen = true }

  async function submitRun(spec) {
    dialogOpen = false; dialogContinueFrom = null
    try {
      if (spec.continueFrom) {
        await api.continueRun(spec.continueFrom, {
          maxTurns: spec.maxTurns,
          stopAt: spec.stopAt ?? null,
          maxSpend: spec.maxSpend ?? null,
          gameplay: spec.gameplay ?? null,
          playerModel: spec.playerModel ?? null,
          taskMasterModel: spec.taskMasterModel ?? null,
          record: spec.record ?? null,
        })
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
  async function toggleMute() {
    const next = !emulator.muted
    emulator = { ...emulator, muted: next }   // optimistic
    try {
      const r = await api.setEmulatorMute(next)
      emulator = { ...emulator, muted: r.muted }   // reconcile with server truth
    } catch (e) {
      console.error('mute toggle failed', e)
      emulator = { ...emulator, muted: !next }      // rollback
    }
  }
  // No global "load a different game" here any more: the game is a property of
  // a RUN, picked in Add run, and the executor loads whichever cartridge the
  // queued item needs before dispatch. `POST /api/emulator/rom` and
  // `api.setEmulatorRom` are deliberately left in place — `pokemon app --rom`
  // and the switch the executor performs still go through that path; it just no
  // longer has a button.
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

{#if view !== 'spectate'}
  <TopBar {active} {emulatorUp} {queue} {view} muted={emulator.muted} ontogglemute={toggleMute}
          {emulator} onnav={go} onspectate={() => go('/spectate')} onnew={openNew} />
{/if}

<main class:kiosk={view === 'spectate'}>
  {#if view === 'home'}
    <QueueBar {active} {queue} onkill={killRun} onremove={removeFromQueue} onreorder={reorder}
      onnew={openNew} onspectate={() => go('/spectate')} />
    <Leaderboard rows={filteredRows} {stats} oninspect={inspect}
      {benchmarks} {benchmark} onbench={selectBenchmark}
      bind:oss={ossFilter} bind:maxPrice={maxPrice} {priceMax} />
    <Charts rows={filteredRows} onpick={(slug) => go(`/history/${slug}`)} />
  {:else if view === 'history'}
    <History {runs} oninspect={inspect} oncontinue={openContinue} ondelete={removeRun} />
  {:else if view === 'spectate'}
    <Spectate run={active} {activeRunId} muted={emulator.muted} ontogglemute={toggleMute} onnew={openNew} onback={() => go('/')}
      {recording} {forcedSimple} />
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

<AddRunDialog open={dialogOpen} continueFrom={dialogContinueFrom} {models} {configs} {benchmarks} {checkpoints} {roms}
  onclose={() => { dialogOpen = false; dialogContinueFrom = null }} onsubmit={submitRun} />

<style>
  main { min-height: calc(100vh - 57px); }
  main.kiosk { height: 100vh; min-height: 0; overflow: hidden; }
  .about { max-width: 680px; margin: 0 auto; padding: 48px 24px; }
  .about h2 { font-size: 26px; font-weight: 780; letter-spacing: -.02em; margin: 0 0 16px; }
  .about p { font-size: 15px; line-height: 1.65; color: var(--muted); }
  .about em { font-style: italic; color: var(--text); }
  .about b { color: var(--text); }
</style>
