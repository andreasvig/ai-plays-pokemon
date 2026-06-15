<script>
  let { active = null, emulatorUp = false, queue = [], view = 'home', onnav, onspectate, onnew } = $props()
  // green/live = a run is active AND the emulator is up; grey/idle otherwise.
  const hasActive = $derived(!!active && emulatorUp)
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
    <button class="btn ghost" class:active={view === 'history'} onclick={() => onnav('/history')}>History</button>
    <button class="btn ghost" class:active={view === 'about'} onclick={() => onnav('/about')}>About</button>
    <button class="btn primary" onclick={() => onnew()}>+ New run</button>
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
</style>
