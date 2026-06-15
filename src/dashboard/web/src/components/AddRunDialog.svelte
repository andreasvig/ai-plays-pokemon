<script>
  import { untrack } from 'svelte'
  // MODELS / CONFIGS are now fed from App (sourced from /api/models + /api/configs)
  // instead of importing the mock module directly.
  // MODELS is now an array of {alias, run_count} objects (api.fetchModels).
  // CONFIGS is a list of config stems, e.g. "config-3.13".
  let { open = false, continueFrom = null, models = [], configs = [], onclose, onsubmit } = $props()
  const MODELS = $derived(models)
  const CONFIGS = $derived(configs)

  // Latest config stem: parse the version number out of the stem (e.g.
  // "config-3.13" → 3.13) and pick the max. /api/configs isn't guaranteed
  // newest-first, so don't trust order. Unparseable stems fall back to the
  // last element of the list.
  function latestConfig(stems) {
    if (!stems || !stems.length) return ''
    // Compare versions COMPONENT-WISE as integers so "3.13" > "3.9"
    // (parseFloat would read 3.13 < 3.9). Split the numeric token on "."
    // into [3,13] and compare element by element against the current best.
    const cmp = (a, b) => {
      const n = Math.max(a.length, b.length)
      for (let i = 0; i < n; i++) {
        const ai = a[i] ?? 0
        const bi = b[i] ?? 0
        if (ai !== bi) return ai - bi
      }
      return 0
    }
    let best = null
    let bestV = null
    for (const s of stems) {
      const m = String(s).match(/(\d+(?:\.\d+)*)/)
      if (!m) continue
      const v = m[1].split('.').map((x) => parseInt(x, 10))
      if (v.some(Number.isNaN)) continue
      if (bestV === null || cmp(v, bestV) > 0) { bestV = v; best = s }
    }
    return best ?? stems[stems.length - 1]
  }

  // continue mode forces casual + locks the model to the source run's model
  let kind = $state('official')
  let model = $state('')           // always the ALIAS string (submit contract)
  let config = $state('')
  let maxTurns = $state(100)        // casual default: 100 turns
  let modelQuery = $state('')       // searchable model-picker filter text

  // Apply open-time defaults ONCE per false→true transition of `open`.
  // Reading `config`/`kind`/`model`/`maxTurns` inside an $effect would make
  // them dependencies, so the effect would re-run on every field edit and
  // re-apply `kind = 'official'` (snapping the segment back). Gate on the
  // `open` transition instead and wrap the field reads/writes in untrack so
  // they never register as dependencies.
  let prevOpen = false
  $effect(() => {
    if (open && !prevOpen) {
      untrack(() => {
        if (continueFrom) {
          kind = 'casual'
          model = continueFrom.model
          maxTurns = continueFrom.maxTurns ?? 100
          if (!config) config = latestConfig(CONFIGS)
        } else {
          kind = 'official'
          model = MODELS[0]?.alias ?? ''
          if (!config) config = latestConfig(CONFIGS)
        }
      })
    }
    prevOpen = open
  })

  const isContinue = $derived(!!continueFrom)
  const isOfficial = $derived(kind === 'official')

  // Searchable, run-count-sorted model list. Models you've already run
  // (higher run_count) sort first so they're easy to find; alias breaks ties.
  const sortedModels = $derived(
    [...MODELS].sort((a, b) => (b.run_count ?? 0) - (a.run_count ?? 0) || a.alias.localeCompare(b.alias))
  )
  const filteredModels = $derived(
    modelQuery.trim()
      ? sortedModels.filter((m) => m.alias.toLowerCase().includes(modelQuery.trim().toLowerCase()))
      : sortedModels
  )

  function submit() {
    onsubmit({
      kind, model,
      config: isOfficial ? 'pokebench-v1' : config,
      maxTurns: isOfficial ? null : maxTurns,
      continueFrom: continueFrom?.runId ?? null,
    })
  }
</script>

{#if open}
  <div class="scrim" onkeydown={(e) => e.key === 'Escape' && onclose()} role="presentation">
    <div class="dialog" role="dialog" aria-modal="true" tabindex="-1">
      <header class="dh">
        <h3>{isContinue ? 'Continue run' : 'Queue a new run'}</h3>
        <button class="x" onclick={() => onclose()} aria-label="Close">✕</button>
      </header>

      {#if isContinue}
        <div class="cont-note">
          Continuing <span class="mono">{continueFrom.runId}</span> from its last save state.
          Continues are always <b>casual</b> and reuse the original model.
        </div>
      {:else}
        <div class="seg">
          <button class:on={isOfficial} onclick={() => kind = 'official'}>
            <b>Official</b><small>benchmark · gated · leaderboard</small>
          </button>
          <button class:on={!isOfficial} onclick={() => kind = 'casual'}>
            <b>Casual</b><small>free config · max-turns · no gates</small>
          </button>
        </div>
      {/if}

      <div class="fields">
        <div class="field">
          <span class="flabel">Model {#if isContinue}<span class="locked">locked</span>{/if}</span>
          {#if isContinue}
            <div class="frozen mono">{model} <span class="faint">(reused from source run)</span></div>
          {:else}
            <input
              type="search"
              class="model-search"
              placeholder="Search models…"
              bind:value={modelQuery}
              autocomplete="off"
            />
            <div class="model-list" role="listbox" aria-label="Model">
              {#each filteredModels as m (m.alias)}
                <button
                  type="button"
                  class="model-row"
                  class:on={m.alias === model}
                  role="option"
                  aria-selected={m.alias === model}
                  onclick={() => model = m.alias}
                >
                  <span class="m-alias">{m.alias}</span>
                  <span class="m-runs">{m.run_count} {m.run_count === 1 ? 'run' : 'runs'}</span>
                </button>
              {:else}
                <div class="model-empty faint">No models match “{modelQuery}”.</div>
              {/each}
            </div>
          {/if}
        </div>

        {#if isOfficial}
          <div class="field">
            <span class="flabel">Config</span>
            <div class="frozen mono">pokebench-v1 <span class="faint">(frozen · gates enforced · no turn cap)</span></div>
          </div>
        {:else}
          <label class="field">
            <span class="flabel">Config</span>
            <select bind:value={config} disabled={isContinue}>
              {#each CONFIGS as c}<option value={c}>{c}</option>{/each}
            </select>
          </label>
          <label class="field">
            <span class="flabel">Max turns</span>
            <input type="number" bind:value={maxTurns} min="1" step="50" />
          </label>
        {/if}
      </div>

      <footer class="df">
        <span class="hint faint">
          {#if isOfficial}Ends on the final gate (win) or a missed deadline.{:else}Runs until max turns. Never on the leaderboard.{/if}
        </span>
        <div class="actions">
          <button class="btn ghost" onclick={() => onclose()}>Cancel</button>
          <button class="btn primary" onclick={submit}>Add to queue</button>
        </div>
      </footer>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed; inset: 0; z-index: 50; background: rgba(16,22,40,.34);
    display: flex; align-items: center; justify-content: center; padding: 24px;
    backdrop-filter: blur(2px);
  }
  .dialog {
    width: 100%; max-width: 460px; background: var(--surface);
    border-radius: 16px; box-shadow: var(--shadow-lg); overflow: hidden;
  }
  .dh { display: flex; align-items: center; justify-content: space-between; padding: 18px 20px 12px; }
  h3 { margin: 0; font-size: 16px; font-weight: 750; }
  .x { border: none; background: none; color: var(--faint); font-size: 14px; padding: 4px 8px; border-radius: 6px; }
  .x:hover { background: #eef1f5; color: var(--text); }

  .cont-note { margin: 0 20px 8px; font-size: 12.5px; color: var(--muted); background: var(--surface-2);
    border: 1px solid var(--border-2); border-radius: 8px; padding: 10px 12px; }

  .seg { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 4px 20px 8px; }
  .seg button {
    display: flex; flex-direction: column; gap: 3px; align-items: flex-start; text-align: left;
    border: 1.5px solid var(--border); background: var(--surface); padding: 12px 14px; border-radius: 10px;
    transition: all .12s;
  }
  .seg button b { font-size: 13.5px; font-weight: 700; }
  .seg button small { font-size: 10.5px; color: var(--faint); }
  .seg button.on { border-color: var(--accent); background: var(--accent-soft); }
  .seg button.on b { color: var(--accent-ink); }
  .seg button.on small { color: var(--accent); }

  .fields { padding: 8px 20px 4px; display: flex; flex-direction: column; gap: 14px; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .flabel { font-size: 11.5px; font-weight: 650; color: var(--muted); display: flex; align-items: center; gap: 8px; }
  .locked { font-size: 9.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); background: #f0f2f6; padding: 1px 6px; border-radius: 4px; }
  select, input {
    font-family: inherit; font-size: 13.5px; padding: 9px 11px; border: 1px solid var(--border);
    border-radius: 8px; background: var(--surface); color: var(--text); width: 100%;
  }
  select:focus, input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  select:disabled, input:disabled { background: var(--surface-2); color: var(--muted); }
  .frozen { font-size: 13px; padding: 9px 11px; border: 1px dashed var(--border); border-radius: 8px; background: var(--surface-2); }

  .model-search { margin-bottom: 6px; }
  .model-list {
    max-height: 168px; overflow-y: auto; border: 1px solid var(--border);
    border-radius: 8px; background: var(--surface); display: flex; flex-direction: column;
  }
  .model-row {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    width: 100%; text-align: left; border: none; background: none;
    padding: 8px 11px; font-family: inherit; font-size: 13px; color: var(--text);
    border-bottom: 1px solid var(--border-2); cursor: pointer; transition: background .1s;
  }
  .model-row:last-child { border-bottom: none; }
  .model-row:hover { background: var(--surface-2); }
  .model-row.on { background: var(--accent-soft); }
  .model-row.on .m-alias { color: var(--accent-ink); font-weight: 650; }
  .m-alias { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .m-runs { flex: none; font-size: 11px; color: var(--faint); }
  .model-row.on .m-runs { color: var(--accent); }
  .model-empty { padding: 12px; font-size: 12px; text-align: center; }

  .df { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 20px 18px; margin-top: 6px; border-top: 1px solid var(--border-2); }
  .hint { font-size: 11.5px; max-width: 220px; line-height: 1.4; }
  .actions { display: flex; gap: 8px; }
</style>
