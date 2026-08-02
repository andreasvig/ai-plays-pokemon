<script>
  import Icon from './Icon.svelte'
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
      <span class="ver mono">pokebench-v1</span>
    </button>
  </div>

  <div class="center">
    <button class="spectate" class:on={hasActive} disabled={!hasActive}
            onclick={() => onspectate()}>
      <span class="dot" class:live={hasActive}></span>
      {hasActive ? 'Spectate' : 'Idle'}
    </button>
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
    <button class="btn ghost mute" class:muted onclick={() => ontogglemute && ontogglemute()}
            title={muted ? 'Game audio muted — click to unmute' : 'Game audio on — click to mute'}
            aria-label={muted ? 'Unmute game audio' : 'Mute game audio'}>
      <Icon name={muted ? 'muted' : 'audio'} size={17} />
    </button>
    <button class="btn ghost" class:active={view === 'history'} onclick={() => onnav('/history')}>History</button>
    <button class="btn ghost" class:active={view === 'about'} onclick={() => onnav('/about')}>About</button>
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
</style>
