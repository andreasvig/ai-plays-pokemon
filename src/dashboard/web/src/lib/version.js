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
//
// A version can have SEVERAL entries: a change that sharpens what is measured
// without moving the scoring rules does not bump the season marker, so entries
// are keyed by date, not by version, and the newest entry carrying
// BENCH_VERSION is the one marked current.
export const CHANGELOG = [
  {
    version: 'pokebench-v1.1',
    label: 'v1.1',
    date: '2026-09-16',
    title: 'In-depth logging: sharper efficiency scores, and a map of every run',
    items: [
      {
        head: 'Every button press is now traced, not just every turn.',
        body: 'The referee records where the player stood before and after each individual input. Steps used to be bounded between one poll a turn, which could only ever give a lower bound; they are now counted exactly. Movement efficiency — shortest walk ÷ steps actually taken — is measured against that count, so it is a real number rather than an upper estimate.',
      },
      {
        head: 'Walking into a wall costs a step.',
        body: 'A press into scenery the player is already facing, where the map has no way through, burns the same frames as a real step and gains no ground — so it is charged like one. That makes efficiency slightly harsher for traced runs than for runs recorded before the trace went live on Sep 14, which are scored on walked steps alone and read a little high. The card marks which runs are which, so the two are never silently compared.',
      },
      {
        head: 'Where every press went.',
        body: 'Each run now breaks its inputs into buckets that divide them exactly: moved the player, pressed in a battle, walked into a wall, turned to face, eaten by a textbox or an NPC, A or B with nothing moving. It is the clearest single view of how much of a run was real play and how much was fumbling.',
      },
      {
        head: 'The route map.',
        body: 'Every run draws the path it actually walked on the game\'s own artwork: one line per visit with its colour turning as the run covers ground, arrows moving along it, and buildings and Viridian Forest as places you can walk into and back out of. Battles are marked where they happened.',
      },
      {
        head: 'Battles are read from the game.',
        body: 'A wild encounter now records the species, its level and how the fight ended — won, ran, caught, lost. A trainer fight carries the trainer and the roster the game gives them. Runs from before Sep 15 have neither and say so rather than guessing an outcome.',
      },
    ],
  },
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
        head: 'A turn cap on every leg replaces the cumulative deadlines.',
        body: 'Each leg between two checkpoints has its own cap, counted from the previous checkpoint: 30 / 30 / 30 / 30 / 30 / 30 / 100 / 50 / 100 / 150 / 300 / 100 turns (no leg under 30; the first cut on Sep 9 ran 30 / 20 / 20 / 20 / 30 / 20 / 60 / 30 / 60 / 100 / 200 / 100). The cap is the only bound: cumulative deadlines let a fast opening bank slack that one section — the Viridian Forest maze — could then burn for 300+ turns without progress; the cap ends such a run within that leg\'s budget. Runs ended this way read "leg cap" on their report.',
      },
      {
        head: 'Cumulative deadlines removed.',
        body: 'The first-badge ladder no longer has a total-turn limit per checkpoint. v1 ran 20 / 30 / 40 / 50 / 65 / 75 / 100 / 120 / 150 / 200 / 300 / 400; a run that reaches Brock within every leg cap has no other clock to beat.',
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
