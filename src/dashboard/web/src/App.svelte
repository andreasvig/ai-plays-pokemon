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
  import * as mock from './lib/mockData.js'

  // route -> view + param
  const parts = $derived(router.path.split('/').filter(Boolean))
  const view = $derived(
    parts[0] === 'spectate' ? 'spectate'
    : parts[0] === 'about' ? 'about'
    : parts[0] === 'history' ? (parts[1] ? 'report' : 'history')
    : 'home'
  )
  const selectedRun = $derived(view === 'report' ? mock.runBySlug[parts[1]] ?? null : null)

  let dialogOpen = $state(false)
  let dialogContinueFrom = $state(null)

  // shared leaderboard/chart filters (lifted so both views respect them)
  const priceMax = Math.max(...mock.leaderboard.map((r) => r.avgCostPerTurn))
  let ossFilter = $state('all')
  let maxPrice = $state(priceMax)
  const filteredRows = $derived(
    mock.leaderboard
      .filter((r) => ossFilter === 'all' || r.openSource)
      .filter((r) => r.avgCostPerTurn <= maxPrice + 1e-9)
  )

  // mutable mock state
  let queue = $state([...mock.queue])
  let active = $state(mock.activeRun)

  const go = (p) => router.navigate(p)
  function inspect(r) { go(`/history/${r.slug}`) }
  function openNew() { dialogContinueFrom = null; dialogOpen = true }
  function openContinue(r) { dialogContinueFrom = r; dialogOpen = true }

  function submitRun(spec) {
    queue = [...queue, { queueId: 'q_' + (queue.length + 20), ...spec }]
    dialogOpen = false; dialogContinueFrom = null
  }
  function killRun() {
    if (queue.length) {
      const [next, ...rest] = queue
      queue = rest
      active = {
        model: next.model, kind: next.kind,
        config: next.kind === 'official' ? 'pokebench-v1' : next.config,
        currentTurn: 0, currentGateDeadline: 25, nextGate: 'left_bedroom',
        gatesReached: 0, totalCostUsd: 0, durationS: 0, avgSPerTurn: 0, status: 'running',
      }
    } else { active = null }
  }
  function removeFromQueue(id) { queue = queue.filter((q) => q.queueId !== id) }
  function reorder(from, to) {
    const next = [...queue]
    const [m] = next.splice(from, 1)
    next.splice(to, 0, m)
    queue = next
  }
</script>

<TopBar {active} {queue} {view} onnav={go} onspectate={() => go('/spectate')} onnew={openNew} />

<main>
  {#if view === 'home'}
    <QueueBar {active} {queue} onkill={killRun} onremove={removeFromQueue} onreorder={reorder}
      onnew={openNew} onspectate={() => go('/spectate')} />
    <Leaderboard rows={filteredRows} stats={mock.stats} oninspect={inspect}
      bind:oss={ossFilter} bind:maxPrice={maxPrice} {priceMax} />
    <Charts rows={filteredRows} onpick={(slug) => go(`/history/${slug}`)} />
  {:else if view === 'history'}
    <History runs={mock.runs} oninspect={inspect} oncontinue={openContinue} />
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

<AddRunDialog open={dialogOpen} continueFrom={dialogContinueFrom}
  onclose={() => { dialogOpen = false; dialogContinueFrom = null }} onsubmit={submitRun} />

<style>
  main { min-height: calc(100vh - 57px); }
  .about { max-width: 680px; margin: 0 auto; padding: 48px 24px; }
  .about h2 { font-size: 26px; font-weight: 780; letter-spacing: -.02em; margin: 0 0 16px; }
  .about p { font-size: 15px; line-height: 1.65; color: var(--muted); }
  .about em { font-style: italic; color: var(--text); }
  .about b { color: var(--text); }
</style>
