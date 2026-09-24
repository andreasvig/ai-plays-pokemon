<script>
  import Icon from './Icon.svelte'
  import { STATIC } from '../lib/static.js'
  import { BENCH_VERSION } from '../lib/version.js'
  let { active = null, emulatorUp = false, queue = [], view = 'home', muted = true, ontogglemute,
        emulator = {}, onnav, onspectate } = $props()
  // green/live = a run is active AND the emulator is up; grey/idle otherwise.
  const hasActive = $derived(!!active && emulatorUp)
</script>

<header class="topbar">
  <div class="left">
    <button class="brand" onclick={() => onnav('/')}>
      <span class="logo"><Icon name="ball" size={20} title="PokeBench" /></span>
      <span class="name">PokeBench</span>
      <span class="ver mono">{BENCH_VERSION}</span>
    </button>
  </div>

  <div class="center">
    <!-- No emulator behind the published site: nothing to spectate, nothing to mute. -->
    {#if !STATIC}
      <button class="spectate" class:on={hasActive} disabled={!hasActive}
              onclick={() => onspectate()}>
        <span class="dot" class:live={hasActive}></span>
        {hasActive ? 'Spectate' : 'Idle'}
      </button>
    {/if}
  </div>

  <nav class="right">
    <!-- The game is chosen per RUN in Add run, not here: the executor loads
         whichever cartridge the queued item needs (`_ensure_rom_loaded`), so a
         separate global switcher could only ever disagree with the run about to
         start. What survives is the one state a person has to ACT on — a ROM
         switch relaunches mGBA and drops the Lua script, and until it is
         re-loaded the emulator is simply unusable, so the app must say so
         rather than going quiet. -->
    {#if emulator.awaiting_lua}
      <span class="romnote">load the Lua script in mGBA</span>
    {/if}
    {#if !STATIC}
      <button class="btn ghost mute" class:muted onclick={() => ontogglemute && ontogglemute()}
              title={muted ? 'Game audio muted — click to unmute' : 'Game audio on — click to mute'}
              aria-label={muted ? 'Unmute game audio' : 'Mute game audio'}>
        <Icon name={muted ? 'muted' : 'audio'} size={17} />
      </button>
    {/if}
    <!-- The public site has no run history: a run is read on its model's page
         (/models/<model>, 2026-09-14). History stays in the local control center
         (continue, delete, the full report). -->
    {#if !STATIC}
      <button class="btn ghost" class:active={view === 'history' || view === 'report'} onclick={() => onnav('/history')}>History</button>
    {/if}
    <button class="btn ghost" class:active={view === 'methods'} onclick={() => onnav('/methods')}>Methodology</button>
    <button class="btn ghost" class:active={view === 'changelog'} onclick={() => onnav('/changelog')}>Changelog</button>
    <button class="btn ghost" class:active={view === 'contact'} onclick={() => onnav('/contact')}>Contact</button>
  </nav>
</header>

<style>
  .topbar {
    position: sticky; top: 0; z-index: 20;
    display: flex; align-items: center; gap: 18px;
    padding: 12px 24px;
    background: rgba(251, 249, 245, .88);
    backdrop-filter: saturate(180%) blur(10px);
    border-bottom: 1px solid var(--border);
  }
  .left, .right { display: flex; align-items: center; gap: 8px; }
  .center { flex: 1; display: flex; align-items: center; gap: 12px; min-width: 0; }

  .brand { display: flex; align-items: center; gap: 9px; border: none; background: none; padding: 0; }
  .logo { color: var(--red); display: grid; place-items: center; }
  .name { font-size: 15px; font-weight: 700; letter-spacing: .02em; }
  .ver { font-size: 10px; letter-spacing: .04em; color: var(--faint); background: var(--wash); padding: 2px 6px; border-radius: var(--radius-sm); }

  .spectate {
    display: inline-flex; align-items: center; gap: 8px;
    border: 1px solid var(--border); background: var(--surface);
    color: var(--faint); font-weight: 650; font-size: 13px;
    padding: 7px 13px; border-radius: var(--radius-sm); transition: all .12s;
  }
  .spectate.on {
    color: var(--green); border-color: var(--green-rule); background: var(--green-soft);
  }
  .spectate.on:hover { border-color: var(--green); }

  .btn.ghost.active { color: var(--text); background: var(--wash); }
  .mute { padding: 6px 8px; display: grid; place-items: center; }
  .mute.muted { opacity: .55; }

  .romnote { font-size: 10.5px; font-weight: 650; color: var(--accent); white-space: nowrap; }

  /* Phone (Andreas 2026-09-17). Brand plus four nav buttons measured 561px on a
     390px screen, so the WHOLE PAGE scrolled sideways — every section under it
     inherited the overflow. The nav takes its own row, and the bar stops being
     sticky: two stacked sticky strips (this and the board's section menu) ate a
     third of the viewport, so the board's menu is the one that pins. */
  @media (max-width: 720px) {
    .topbar { position: static; flex-wrap: wrap; gap: 6px 10px; padding: 10px 10px 0; }
    .center { flex: 0 1 auto; }
    .right { flex: 1 0 100%; justify-content: flex-start; gap: 2px; overflow-x: auto; padding-bottom: 2px; }
    .right .btn { padding: 6px 9px; font-size: 12.5px; white-space: nowrap; }
    .name { font-size: 14px; }
  }
</style>
