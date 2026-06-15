<script>
  import { GATES, gate, TOTAL_GATES } from '../lib/gates.js'
  import { usd, dur } from '../lib/format.js'
  import JsonTree from './JsonTree.svelte'
  import TraceFeed from './TraceFeed.svelte'
  let { run = null, onnew, onback } = $props()

  const reached = $derived(run ? run.gatesReached : 0)
  const turnsLeft = $derived(run ? run.currentGateDeadline - run.currentTurn : 0)
  const budgetPct = $derived(run ? Math.min(100, run.currentTurn / run.currentGateDeadline * 100) : 0)
  const tone = $derived(turnsLeft < 25 ? 'red' : turnsLeft < 60 ? 'amber' : 'ok')

  // mini gate ladder window around the current rung
  const ladder = $derived(GATES
    .map((g, i) => ({ ...g, status: i < reached ? 'done' : i === reached ? 'current' : 'upcoming',
                      turn: i < reached ? Math.round(g.deadline * 0.74) : null }))
    .slice(Math.max(0, reached - 2), reached + 3))

  const memory = {
    location: 'Mt. Moon B1F',
    party: [
      { species: 'CHARMELEON', lvl: 22 },
      { species: 'PIDGEY', lvl: 12 },
      { species: 'NIDORAN♂', lvl: 14 },
    ],
    badges: ['Boulder', 'Cascade'],
    money: 4210,
    items: ['Potion x4', 'Antidote', 'Moon Stone', 'Escape Rope'],
    objective: 'reach Route 4 via Mt. Moon',
  }
  const task = {
    title: 'Cross Mt. Moon to Route 4',
    description: 'Navigate the three-floor cave, handle the Super Nerd and Rocket grunts, then exit north to Route 4. Pick up a fossil if convenient.',
    success: 'Player map = Route 4 (north exit)',
  }

  const feed = [
    { turn: 631, boxes: [
      { k: 'thinking', t: "On Mt. Moon B1F near the eastern ladder. The Rocket grunt blocks the corridor to Route 4 — I'll engage and push through." },
      { k: 'action', t: '↑ ↑ → → a ↑' },
      { k: 'tool', name: 'press_buttons', args: 'buttons=[up,up,right,right,a,up]', resp: 'ok · screen advanced' },
      { k: 'ocr', t: 'ROCKET wants to fight!', meta: '2 captures · 0.4s · $0.0003' },
      { k: 'output', ok: null, t: 'Engaged the grunt; battle starting.' },
      { k: 'settle', t: 'screen settled in 1.1s' },
    ] },
    { turn: 630, boxes: [
      { k: 'thinking', t: 'Picked up the Moon Stone. The southern fork is a dead end — heading back north.' },
      { k: 'memory', t: '{ items: [+Moon Stone], floor: "B1F" }' },
      { k: 'output', ok: true, t: 'Backtracked north past the boulder.' },
    ] },
    { turn: 629, boxes: [
      { k: 'thinking', t: 'Wild Zubat encounter — run, conserving PP for the grunt ahead.' },
      { k: 'action', t: 'down b b' },
      { k: 'output', ok: true, t: 'Fled the battle.' },
    ] },
  ]
</script>

<section class="wrap">
  {#if !run}
    <div class="empty">
      <p class="big">No active run</p>
      <p class="faint">Queue a run to start spectating — the next item starts automatically.</p>
      <button class="btn" onclick={() => onnew()}>+ New run</button>
      <button class="btn ghost" onclick={() => onback()}>← Back to leaderboard</button>
    </div>
  {:else}
    <div class="bar">
      <button class="btn ghost" onclick={() => onback()}>← Leaderboard</button>
      <span class="pill"><span class="dot live"></span> live</span>
      <span class="badge official">benchmark</span>
      <span class="model mono">{run.model}</span>
      <span class="conn">
        <span class="c-ind"><span class="dot live"></span> screen</span>
        <span class="c-ind"><span class="dot live"></span> events</span>
        <span class="c-evt faint mono">1,284 events</span>
      </span>
    </div>

    <div class="layout">
      <!-- main: BIG emulator + HUD + stats + task + memory -->
      <div class="main">
        <div class="stats">
          <div class="stat"><span class="sl">Turn</span><span class="sv tnum">{run.currentTurn}</span></div>
          <div class="stat"><span class="sl">Cost</span><span class="sv tnum">{usd(run.totalCostUsd)}</span></div>
          <div class="stat"><span class="sl">Tokens</span><span class="sv tnum">412k<span class="su">/89k</span></span></div>
          <div class="stat"><span class="sl">Elapsed</span><span class="sv tnum">{dur(run.durationS)}</span></div>
          <div class="stat"><span class="sl">Gates</span><span class="sv tnum">{reached}/{TOTAL_GATES}</span></div>
        </div>

        <div class="gba"><div class="ph">emulator screen<br /><span class="faint">live stream (mock)</span></div></div>

        <div class="panels">
          <div class="panel task">
            <div class="p-h">🧭 Current task</div>
            <div class="p-scroll">
              <div class="t-title">{task.title}</div>
              <div class="t-lab">Description</div>
              <p class="t-body">{task.description}</p>
              <div class="t-lab">🎯 Success criteria</div>
              <p class="t-body mono">{task.success}</p>
            </div>
          </div>
          <div class="panel mem">
            <div class="p-h">🧠 Memory dictionary</div>
            <div class="p-scroll"><JsonTree data={memory} /></div>
          </div>
        </div>

        <div class="hud {tone}">
          <div class="hud-top">
            <span class="hl">Next gate</span>
            <span class="hv">{gate(run.nextGate)?.name}</span>
            <span class="hv-left tnum">{turnsLeft} turns left · limit T{run.currentGateDeadline}</span>
          </div>
          <div class="hud-track"><span class="hud-fill" style={`width:${budgetPct}%`}></span></div>
          <div class="ladder">
            {#each ladder as g}
              <div class="lg {g.status}">
                <span class="lg-i">{g.status === 'done' ? '✓' : g.status === 'current' ? '▶' : '·'}</span>
                <span class="lg-n">{g.name}</span>
                <span class="lg-t tnum faint">{g.turn != null ? 'T' + g.turn : 'T' + g.deadline}</span>
              </div>
            {/each}
          </div>
        </div>
      </div>

      <!-- side: live trace feed (own component) -->
      <TraceFeed turns={[...feed].reverse()} />
    </div>
  {/if}
</section>

<style>
  .wrap { max-width: 1320px; margin: 0 auto; padding: 22px 24px; }
  .empty { text-align: center; padding: 80px 0; display: flex; flex-direction: column; gap: 10px; align-items: center; }
  .big { font-size: 18px; font-weight: 700; margin: 0; }
  .bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
  .bar .model { font-size: 14px; font-weight: 650; }
  .conn { margin-left: auto; display: flex; align-items: center; gap: 12px; }
  .c-ind { font-size: 11px; font-weight: 650; color: var(--green); display: inline-flex; align-items: center; gap: 5px; }
  .c-evt { font-size: 11px; }

  .layout { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 20px; align-items: start; }
  .main { display: flex; flex-direction: column; gap: 14px; }
  .gba { aspect-ratio: 240/160; background: #11141b; border-radius: var(--radius); display: flex; align-items: center; justify-content: center; box-shadow: var(--shadow-lg); }
  .ph { color: #7b8696; text-align: center; font-size: 16px; line-height: 1.7; }

  .hud { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; box-shadow: var(--shadow); }
  .hud.amber { border-color: #f0d9a0; } .hud.red { border-color: #f0c5c5; }
  .hud-top { display: flex; align-items: baseline; gap: 12px; }
  .hl { font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); font-weight: 700; }
  .hv { font-size: 14px; font-weight: 700; }
  .hv-left { margin-left: auto; font-size: 12.5px; color: var(--muted); font-weight: 650; }
  .hud-track { height: 7px; background: #eef1f5; border-radius: 4px; overflow: hidden; margin: 10px 0; }
  .hud-fill { display: block; height: 100%; background: var(--accent); }
  .hud.amber .hud-fill { background: var(--amber); } .hud.red .hud-fill { background: var(--red); }
  .ladder { display: flex; flex-direction: column; gap: 1px; border-top: 1px solid var(--border-2); padding-top: 8px; }
  .lg { display: grid; grid-template-columns: 18px 1fr auto; gap: 8px; align-items: center; font-size: 12px; padding: 2px 0; }
  .lg-i { text-align: center; font-weight: 800; color: var(--faint); }
  .lg.done .lg-i { color: var(--green); }
  .lg.current { font-weight: 700; }
  .lg.current .lg-i { color: var(--accent); }
  .lg.upcoming { color: var(--muted); }
  .lg-t { font-size: 11px; }

  .stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
  .stat { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 11px 12px; box-shadow: var(--shadow); }
  .sl { display: block; font-size: 9.5px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .sv { font-size: 18px; font-weight: 750; }
  .su { font-size: 11px; color: var(--muted); font-weight: 600; }

  .panels { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; box-shadow: var(--shadow); }
  .p-h { font-size: 11px; font-weight: 750; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin-bottom: 10px; }
  .t-title { font-size: 14px; font-weight: 700; margin-bottom: 8px; }
  .t-lab { font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; margin-top: 8px; }
  .t-body { font-size: 12.5px; line-height: 1.5; color: var(--muted); margin: 3px 0 0; }
  .p-scroll { max-height: 210px; overflow-y: auto; overflow-x: auto; padding-right: 4px; }

  @media (max-width: 1080px) { .layout { grid-template-columns: 1fr; } .panels { grid-template-columns: 1fr; } }
</style>
