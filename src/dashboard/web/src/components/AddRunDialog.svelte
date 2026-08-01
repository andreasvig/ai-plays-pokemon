<script>
  import { untrack } from 'svelte'
  import { searchModels } from '../lib/modelSearch.js'
  // MODELS / CONFIGS are fed from App (sourced from /api/models + /api/configs).
  // MODELS is the collapsed registry: [{model, openrouter_id, reasoning_type,
  // default_level, levels:[{level, observed, run_count}], observed, run_count}].
  // Pick a model, then a thinking level (default = highest); submit "model(level)".
  // CONFIGS is a list of config stems, e.g. "config-3.13".
  // CHECKPOINTS is the full ladder flattened ([{id, name, type}]) — the story
  // events a casual run can be told to stop at, from /api/checkpoints.
  let { open = false, continueFrom = null, models = [], configs = [], benchmarks = [], checkpoints = [], onclose, onsubmit } = $props()
  const MODELS = $derived(models)
  const CONFIGS = $derived(configs)
  const BENCHMARKS = $derived(benchmarks)
  const CHECKPOINTS = $derived(checkpoints)
  // The registry-default benchmark (or the first), pre-selected on open.
  const defaultBenchmark = $derived(BENCHMARKS.find((b) => b.default)?.id ?? BENCHMARKS[0]?.id ?? '')

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
  let modelBase = $state('')        // selected model name (e.g. "gpt-5.5")
  let level = $state('')            // selected thinking level (e.g. "high"); '' = none
  let config = $state('')
  let benchmark = $state('')        // selected benchmark id (official only)
  let maxTurns = $state(100)        // casual default: 100 turns
  // Casual early finish line. '' = none (turn cap only), which stays the
  // default: a stop event is a deliberate "play until X", never a surprise.
  // Set alongside max turns, not instead of it — whichever lands first wins.
  let stopAt = $state('')

  // ── recording (opt-in; off by default — it costs a headless browser + an
  // encoder for the whole run, so it is never something you get by accident) ──
  // Defaults are simple + cut-thinking: the pairing you'd actually post. Ticking
  // the box should give you a postable clip, not a ten-minute file of a model
  // thinking. The CLI still defaults to realtime, where the caller is scripting
  // and an unasked-for edit is the surprising outcome.
  let record = $state(false)
  let recordView = $state('simple')          // the 1:1 recording view
  let recordSpeed = $state('cut-thinking')   // execution windows only
  let modelQuery = $state('')       // searchable model-picker filter text
  // Casual-continue TaskMaster override. '' = keep the source run's TaskMaster
  // (the backend reuses it); otherwise a "model(level)" alias to switch to.
  let taskMasterChoice = $state('')

  // Split a "model(level)" alias into its parts (level '' for type-none models).
  function parseAlias(alias) {
    const m = String(alias ?? '').match(/^(.*?)\((.*)\)$/)
    return m ? { base: m[1], level: m[2] } : { base: alias ?? '', level: '' }
  }

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
          // Continue INHERITS the source run's kind: an official run continues
          // official on the SAME benchmark (still leaderboard-eligible); a casual
          // run continues casual. The backend (build_continue_spec) is
          // authoritative — this just mirrors it so the dialog isn't misleading.
          kind = continueFrom.kind === 'official' ? 'official' : 'casual'
          benchmark = continueFrom.benchmark ?? defaultBenchmark
          taskMasterChoice = ''  // default: keep the source TaskMaster
          if (continueFrom.kind === 'official') {
            // Official continue is model-LOCKED — reuse the source identity
            // verbatim, no picker (it must stay leaderboard-comparable).
            modelBase = ''
            level = ''
          } else {
            // Casual continue: seed the Player picker to the source model so
            // "reuse" is the default; the user can change Player and/or TaskMaster.
            const parsed = parseAlias(continueFrom.model)
            modelBase = parsed.base
            level = parsed.level
          }
          maxTurns = continueFrom.maxTurns ?? 100
          if (!config) config = latestConfig(CONFIGS)
        } else {
          kind = 'official'
          const first = sortedModels[0]
          modelBase = first?.model ?? ''
          level = first?.default_level ?? ''   // default = highest level
          if (!config) config = latestConfig(CONFIGS)
          benchmark = defaultBenchmark
        }
      })
    }
    prevOpen = open
  })

  const isContinue = $derived(!!continueFrom)
  const isOfficial = $derived(kind === 'official')
  // The Player model is locked (no picker) ONLY on an official continue. A casual
  // continue gets the full picker (seeded to the source model); fresh runs always do.
  const lockModel = $derived(isContinue && isOfficial)
  // Casual continue is the one mode that exposes a TaskMaster override picker.
  const casualContinue = $derived(isContinue && !isOfficial)
  const selectedBench = $derived(BENCHMARKS.find((b) => b.id === benchmark) ?? null)

  // The picked model row + its thinking levels (second-axis dropdown).
  const selectedModel = $derived(MODELS.find((m) => m.model === modelBase) ?? null)
  const availableLevels = $derived(selectedModel?.levels ?? [])

  // Final Player identity: official continue reuses the source alias verbatim;
  // otherwise (fresh OR casual continue) it's the picker's "model(level)", or the
  // bare model when it has no thinking levels (type none).
  const model = $derived(
    lockModel
      ? (continueFrom?.model ?? '')
      : (level ? `${modelBase}(${level})` : modelBase)
  )

  // Pick a model: set it AND snap the level to that model's default (highest).
  // Done in the click handler (not an $effect) so changing model can't get into
  // a reactive loop re-applying the default over a manual level edit.
  function pickModel(m) {
    modelBase = m.model
    level = m.default_level ?? ''
  }

  // Newest model first. Release dates come from OpenRouter via
  // configs/model_release_dates.json (see scripts/sync_model_release_dates.py);
  // a model with no date sorts last rather than being guessed at, and name
  // breaks ties so the order is stable when two models shipped the same day.
  function byRelease(a, b) {
    const ra = a.released ?? ''
    const rb = b.released ?? ''
    if (ra !== rb) return rb.localeCompare(ra)
    return a.model.localeCompare(b.model)
  }
  const sortedModels = $derived([...MODELS].sort(byRelease))
  // The Player plays from screenshots, so its picker only offers MULTIMODAL
  // models (the guard). Every current model qualifies, so this is future-proofing.
  const playerModels = $derived(sortedModels.filter((m) => m.multimodal !== false))
  // Fuzzy: typos and missing separators are forgiven, digits are not. See
  // lib/modelSearch.js — "gbt 5.1" finds gpt-5.1 and must NOT find gpt-4.1.
  const filteredModels = $derived(searchModels(playerModels, modelQuery, byRelease))
  // TaskMaster override options (casual continue). It reasons over text handoffs,
  // so it isn't multimodal-gated; offer every model at its default level, plus a
  // "keep original" sentinel ('').
  const tmOptions = $derived(
    sortedModels.map((m) => ({
      alias: m.default_level ? `${m.model}(${m.default_level})` : m.model,
      label: m.default_level ? `${m.model} · ${m.default_level}` : m.model,
    }))
  )

  function submit() {
    onsubmit({
      kind, model,
      benchmark: isOfficial ? benchmark : null,
      config: isOfficial ? null : config,
      maxTurns: isOfficial ? null : maxTurns,
      // Official ends at its own ladder, so a stop event is casual-only.
      stopAt: isOfficial ? null : (stopAt || null),
      continueFrom: continueFrom?.runId ?? null,
      // Casual continue may override models. Player rides on `model` (the backend
      // treats it as reuse when it equals the source alias, else an override).
      // TaskMaster: '' = keep the source's. Both null for fresh/official.
      playerModel: casualContinue ? model : null,
      taskMasterModel: casualContinue && taskMasterChoice ? taskMasterChoice : null,
      // Opt-in MP4 capture. null (not false) when off, because the backend
      // treats an absent spec as "don't record" and validates a present one.
      record: record ? { view: recordView, speed: recordSpeed } : null,
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
          Continuing <span class="mono">{continueFrom.runId}</span> from its last save state — on the exact turn it left off.
          {#if isOfficial}
            This stays an <b>official</b> run on the <b>{selectedBench?.name ?? continueFrom.benchmark}</b> benchmark — it can still complete the ladder and post to the leaderboard. Models are <b>locked</b> to the source run.
          {:else}
            Continues <b>casual</b>. Defaults to the source run's models — change the Player and/or TaskMaster below to resume on a different model.
          {/if}
        </div>
      {:else}
        <div class="seg">
          <button class:on={isOfficial} onclick={() => kind = 'official'}>
            <b>Benchmark</b><small>gated · leaderboard</small>
          </button>
          <button class:on={!isOfficial} onclick={() => kind = 'casual'}>
            <b>Casual</b><small>free config · max-turns · no gates</small>
          </button>
        </div>
      {/if}

      <div class="fields">
        <div class="field">
          <span class="flabel">{casualContinue ? 'Player model' : 'Model'} {#if lockModel}<span class="locked">locked</span>{/if}</span>
          {#if lockModel}
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
              {#each filteredModels as m (m.model)}
                <button
                  type="button"
                  class="model-row"
                  class:on={m.model === modelBase}
                  role="option"
                  aria-selected={m.model === modelBase}
                  onclick={() => pickModel(m)}
                >
                  <span class="m-alias">{m.model}</span>
                  <span class="m-runs">{m.run_count} {m.run_count === 1 ? 'run' : 'runs'}</span>
                </button>
              {:else}
                <div class="model-empty faint">No models match “{modelQuery}”.</div>
              {/each}
            </div>
          {/if}
        </div>

        {#if !lockModel && availableLevels.length}
          <label class="field">
            <span class="flabel">Thinking level <span class="faint">· benchmarked separately</span></span>
            <select bind:value={level}>
              {#each availableLevels as lv}
                <option value={lv.level}>{lv.level}{#if lv.run_count} · {lv.run_count} {lv.run_count === 1 ? 'run' : 'runs'}{/if}</option>
              {/each}
            </select>
          </label>
        {/if}

        {#if casualContinue}
          <label class="field">
            <span class="flabel">TaskMaster model</span>
            <select bind:value={taskMasterChoice}>
              <option value="">Keep original</option>
              {#each tmOptions as o}<option value={o.alias}>{o.label}</option>{/each}
            </select>
            <span class="faint tm-hint">Plans tasks &amp; handoffs from text. Gemini Flash is the reliable default for casual runs.</span>
          </label>
        {/if}

        {#if isOfficial}
          <label class="field">
            <span class="flabel">Benchmark {#if isContinue}<span class="locked">locked</span>{/if}</span>
            <select bind:value={benchmark} disabled={isContinue}>
              {#each BENCHMARKS as b}<option value={b.id}>{b.name}</option>{/each}
            </select>
          </label>
          {#if selectedBench}
            <p class="goal">{selectedBench.goal}</p>
          {/if}
          <div class="field">
            <span class="flabel">Config</span>
            <div class="frozen mono">config-3.13 <span class="faint">(frozen · gates enforced · no turn cap)</span></div>
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
          <!-- Optional early finish line. The ids come from the real gate
               ladder (/api/checkpoints) — the same events the referee stamps
               for a benchmark — so "play until Viridian Forest" is detected
               from game memory, not guessed from the screen. -->
          <label class="field">
            <span class="flabel">Stop at</span>
            <select bind:value={stopAt}>
              <option value="">— none (run to max turns)</option>
              {#each CHECKPOINTS as c}<option value={c.id}>{c.name}</option>{/each}
            </select>
          </label>
          {#if stopAt}
            <p class="rechint faint">
              Ends the run as soon as the referee detects this — or at {maxTurns} turns, whichever comes first.
            </p>
          {/if}
        {/if}

        <!-- Recording. Rendered headlessly server-side, so it keeps going
             whatever this browser is doing — that is the whole point of it
             living on the run spec rather than in a screen-recorder. -->
        <label class="check">
          <input type="checkbox" bind:checked={record} />
          <span>Record this run to MP4</span>
        </label>
        {#if record}
          <div class="recopts">
            <label class="field">
              <span class="flabel">Capture</span>
              <select bind:value={recordView}>
                <option value="simple">Simple view · 1:1 · screen + turn box</option>
                <option value="detailed">Detailed view · 1920×1080 · full panel</option>
              </select>
            </label>
            <label class="field">
              <span class="flabel">Speed</span>
              <select bind:value={recordSpeed}>
                <option value="realtime">Real time · every pause kept</option>
                <option value="cut-thinking">Cut thinking · execution only</option>
              </select>
            </label>
            <p class="rechint faint">
              {#if recordSpeed === 'cut-thinking'}
                Records each turn from the moment it starts executing until the screen settles — the model's response time is left out.
              {:else}
                Records continuously, including the time the model spends thinking.
              {/if}
              Saved to <span class="mono">recording.mp4</span> in the run folder.
            </p>
          </div>
        {/if}
      </div>

      <footer class="df">
        <span class="hint faint">
          {#if isOfficial}Ends on the final gate (win) or a missed deadline.{:else if stopAt}Ends at the chosen event or max turns. Never on the leaderboard.{:else}Runs until max turns. Never on the leaderboard.{/if}
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
  .check { display: flex; align-items: center; gap: 8px; font-size: 12.5px;
    color: var(--text); font-weight: 600; cursor: pointer; margin-top: 2px; }
  .check input { width: 14px; height: 14px; accent-color: var(--accent, #3b82f6); }
  .recopts { display: flex; flex-direction: column; gap: 10px;
    border-left: 2px solid var(--border-2); padding-left: 12px; margin-left: 3px; }
  .rechint { font-size: 11.5px; line-height: 1.5; margin: 0; }

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
  .goal { margin: -4px 0 0; font-size: 12px; line-height: 1.45; color: var(--muted); font-style: italic; }
  .tm-hint { font-size: 10.5px; line-height: 1.4; }

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
