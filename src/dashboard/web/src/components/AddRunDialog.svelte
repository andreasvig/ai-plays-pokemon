<script>
  import { MODELS, CONFIGS } from '../lib/mockData.js'
  let { open = false, continueFrom = null, onclose, onsubmit } = $props()

  // continue mode forces casual + locks the model to the source run's model
  let kind = $state('official')
  let model = $state(MODELS[0])
  let config = $state(CONFIGS[0])
  let maxTurns = $state(1500)

  $effect(() => {
    if (open && continueFrom) {
      kind = 'casual'
      model = continueFrom.model
      maxTurns = continueFrom.maxTurns ?? 1500
    } else if (open && !continueFrom) {
      kind = 'official'
      model = MODELS[0]
    }
  })

  const isContinue = $derived(!!continueFrom)
  const isOfficial = $derived(kind === 'official')

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
  <div class="scrim" onclick={() => onclose()} onkeydown={(e) => e.key === 'Escape' && onclose()} role="presentation">
    <div class="dialog" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" tabindex="-1">
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
        <label class="field">
          <span class="flabel">Model {#if isContinue}<span class="locked">locked</span>{/if}</span>
          <select bind:value={model} disabled={isContinue}>
            {#each MODELS as m}<option value={m}>{m}</option>{/each}
          </select>
        </label>

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

  .df { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 20px 18px; margin-top: 6px; border-top: 1px solid var(--border-2); }
  .hint { font-size: 11.5px; max-width: 220px; line-height: 1.4; }
  .actions { display: flex; gap: 8px; }
</style>
