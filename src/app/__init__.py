"""Domain + persistence layer for the PokeBench Local Control Center.

This package is the headless backend foundation (Plan §P1): the run-kind/state
enums and the flat ``RunSummary`` index entry (``models``), a projection from
each run's nested ``run_summary.json`` to that flat shape (``projection``), the
``runs_index.json``-backed :class:`RunIndex` (``run_index``), the
``queue.json``-backed :class:`QueueManager` (``queue_manager``), and the pure
leaderboard/history derivations over a list of summaries (``derivations``).

Everything here is filesystem-backed JSON and free of UI / web / emulator
concerns, so the index is fully rebuildable by scanning ``local/runs/*``.
"""
