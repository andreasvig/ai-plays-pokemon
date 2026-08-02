<script>
  let { active = null, emulatorUp = false, queue = [], view = 'home', muted = true, ontogglemute,
        roms = [], emulator = {}, onrom, onnav, onspectate } = $props()
  // green/live = a run is active AND the emulator is up; grey/idle otherwise.
  const hasActive = $derived(!!active && emulatorUp)

  // Which game is in the slot, and whether it can be changed right now. Only
  // shown when there is more than one playable ROM on this machine.
  const playable = $derived(roms.filter((r) => r.on_disk !== false))
  const currentRom = $derived(emulator.rom?.id ?? '')
  const switching = $derived(!!emulator.switching_to)
  // A run holds the cartridge; so does an unfinished switch (the Lua script has
  // not been re-loaded yet, so the emulator isn't usable either way).
  const romLocked = $derived(switching || !!emulator.busy || !!emulator.awaiting_lua)
  const showRom = $derived(playable.length > 1 && !!emulator.configured)
</script>

<header class="topbar">
  <div class="left">
    <button class="brand" onclick={() => onnav('/')}>
      <span class="logo">◓</span>
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
    <!-- Which game the emulator is holding. Changing it relaunches mGBA, which
         drops the Lua script — hence the explicit "load the script" state
         rather than the app just going quiet. -->
    {#if showRom}
      <span class="rom" class:pending={romLocked}>
        <select value={currentRom} disabled={romLocked}
                onchange={(e) => onrom && onrom(e.currentTarget.value)}
                title={romLocked ? 'Busy — the emulator is in use' : 'Load a different game'}
                aria-label="Game">
          {#each playable as r}<option value={r.id}>{r.name}</option>{/each}
        </select>
        {#if emulator.awaiting_lua}
          <span class="romnote">load the Lua script in mGBA</span>
        {/if}
      </span>
    {/if}
    <button class="btn ghost mute" class:muted onclick={() => ontogglemute && ontogglemute()}
            title={muted ? 'Game audio muted — click to unmute' : 'Game audio on — click to mute'}
            aria-label={muted ? 'Unmute game audio' : 'Mute game audio'}>
      {muted ? '🔇' : '🔊'}
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
    background: rgba(255,255,255,.82);
    backdrop-filter: saturate(180%) blur(10px);
    border-bottom: 1px solid var(--border);
  }
  .left, .right { display: flex; align-items: center; gap: 8px; }
  .center { flex: 1; display: flex; align-items: center; gap: 12px; min-width: 0; }

  .brand { display: flex; align-items: center; gap: 9px; border: none; background: none; padding: 0; }
  .logo { font-size: 20px; color: var(--accent); line-height: 1; }
  .name { font-size: 16px; font-weight: 750; letter-spacing: -.01em; }
  .ver { font-size: 10.5px; color: var(--faint); background: #f0f2f6; padding: 2px 6px; border-radius: 5px; }

  .spectate {
    display: inline-flex; align-items: center; gap: 8px;
    border: 1px solid var(--border); background: var(--surface);
    color: var(--faint); font-weight: 650; font-size: 13px;
    padding: 7px 14px; border-radius: 999px; transition: all .12s;
  }
  .spectate.on {
    color: var(--green); border-color: #bfe6cc; background: var(--green-soft);
  }
  .spectate.on:hover { box-shadow: 0 0 0 4px var(--green-soft); }

  .btn.ghost.active { color: var(--text); background: #eef1f5; }
  .mute { font-size: 15px; line-height: 1; padding: 6px 10px; }
  .mute.muted { opacity: .55; }

  .rom { display: inline-flex; align-items: center; gap: 8px; }
  .rom select {
    font-family: inherit; font-size: 12px; font-weight: 600; color: var(--text);
    padding: 5px 8px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--surface); max-width: 170px;
  }
  .rom select:disabled { color: var(--muted); background: var(--surface-2); }
  .rom.pending select { border-style: dashed; }
  .romnote { font-size: 10.5px; font-weight: 650; color: var(--accent, #3b82f6); white-space: nowrap; }
</style>
