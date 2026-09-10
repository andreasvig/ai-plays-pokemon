// The benchmark's public version, and what changed between versions.
//
// BENCH_VERSION mirrors OFFICIAL_BENCHMARK_VERSION in src/app/executor.py — the
// season marker the executor stamps on every finished official run. A Python
// test (tests/test_benchmark_version_sync.py) pins the pair so the badge in the
// top bar can never claim a version the runs are not stamped with.
export const BENCH_VERSION = 'pokebench-v1.1'
export const BENCH_LABEL = 'PokeBench v1.1'

// Newest first. `version` is the season marker runs carry; `date` is when the
// change went live on the public site. Keep entries to what a reader of the
// board needs to compare rows across versions — not a commit log.
export const CHANGELOG = [
  {
    version: 'pokebench-v1.1',
    label: 'v1.1',
    date: '2026-09-09',
    title: 'More room at every checkpoint, partial credit between them, and a cap per leg',
    items: [
      {
        head: 'Official harness moved to config-5.1.',
        body: 'Official runs now play on config-5.1: the same append-and-compact agent as 5.0, with a plainer gameplay output — the model returns its inputs and its reasoning, and is no longer forced to grade its previous turn with a true/false verdict. The prediction discipline lives in the reasoning instead (compare the screen with last turn\'s prediction, end with a concrete one). Rows show which config they ran on; config-5.0 rows stay ranked beside 5.1 ones.',
      },
      {
        head: 'A turn cap on every leg.',
        body: 'Besides the cumulative deadline, each leg between two checkpoints now has its own cap, counted from the previous checkpoint: 30 / 30 / 30 / 30 / 30 / 30 / 100 / 30 / 100 / 150 / 300 / 100 turns (no leg under 30; the first cut on Sep 9 ran 30 / 20 / 20 / 20 / 30 / 20 / 60 / 30 / 60 / 100 / 200 / 100). Cumulative deadlines let a fast opening bank slack that one section — the Viridian Forest maze — could then burn for 300+ turns without progress; the cap ends such a run within that leg\'s budget. Runs ended this way read "leg cap" on their report.',
      },
      {
        head: 'Cumulative deadlines are now the sum of the leg caps.',
        body: 'The first-badge ladder no longer has a separate total-turn limit per checkpoint: each deadline equals the running sum of the leg caps (T30 / 60 / 90 / 120 / 150 / 180 / 280 / 310 / 410 / 560 / 860 / 960), so the per-leg cap is the only bound a run can hit. v1 ran 20 / 30 / 40 / 50 / 65 / 75 / 100 / 120 / 150 / 200 / 300 / 400.',
      },
      {
        head: 'Partial completion between checkpoints.',
        body: 'A run that stops between two gates is credited for how far it walked toward the next one: the referee polls the player\'s position every turn and measures path distance on the game\'s tile map (ledges one-way, the Forest maze real, doors joining maps). Completion % and the ranking use this, so two runs that died at the same gate no longer tie.',
      },
      {
        head: 'Oak\'s Lab is measured to Oak\'s trigger.',
        body: 'The lab checkpoint is reached by walking north until Professor Oak stops you, so the distance for that leg now targets his trigger tiles on Pallet Town\'s north edge rather than the lab door.',
      },
      {
        head: 'v1 runs stay on the board.',
        body: 'A run that met the tighter v1 deadlines also meets the v1.1 ones, and the ranking is on progress and turns, not on the deadlines. Rows from v1 are marked.',
      },
    ],
  },
  {
    version: 'pokebench-v1',
    label: 'v1',
    date: '2026-09-08',
    title: 'Launch',
    items: [
      {
        head: 'First-badge ladder.',
        body: 'Twelve story checkpoints from the bedroom to the Boulder Badge, each with a turn deadline; a run that falls behind is terminated. Scored on gates cleared, then fewest turns.',
      },
      {
        head: 'One harness, one config.',
        body: 'Every official run uses the same vision-only harness, the same frozen config, the same ROM and the same starting save. The model is the only variable.',
      },
    ],
  },
]
