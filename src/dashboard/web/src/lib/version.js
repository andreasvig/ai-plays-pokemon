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
        head: 'A turn cap on every leg.',
        body: 'Besides the cumulative deadline, each leg between two checkpoints now has its own cap, counted from the previous checkpoint: 30 / 20 / 20 / 20 / 30 / 20 / 60 / 30 / 60 / 100 / 200 / 100 turns. Whichever bound is hit first ends the run. Cumulative deadlines let a fast opening bank slack that one section — the Viridian Forest maze — could then burn for 300+ turns without progress; the cap ends such a run within that leg\'s budget. The caps sum to 690 against a 600-turn ladder, so a run pacing normally only ever meets the deadline. Runs ended this way read "leg cap" on their report.',
      },
      {
        head: 'Checkpoint deadlines raised.',
        body: 'Every gate on the first-badge ladder got more turns: 30 / 40 / 50 / 65 / 85 / 100 / 150 / 180 / 220 / 300 / 500 / 600, up from 20 / 30 / 40 / 50 / 65 / 75 / 100 / 120 / 150 / 200 / 300 / 400. Each leg now allows at least twice the turns the first full clear needed, with extra absolute slack on the opening rungs.',
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
