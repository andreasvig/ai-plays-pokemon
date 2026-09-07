"""Domain enums + pydantic models for the control center (Plan §P1).

``RunSummary`` is the FLAT, denormalized index entry — one row in
``runs_index.json`` and the unit the leaderboard/history derivations operate on.
It is *projected* from each run's nested ``run_summary.json`` (see
``projection.project_run_dir``); the nested writer in ``agent/turn.py`` stays the
source of truth and is never flattened in place.

``QueuedRun`` is what "add new run" produces — the unit the queue persists.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RunKind(str, Enum):
    """The only run-classification axis (locked decision #4).

    ``official`` runs are the frozen benchmark (gates enforced, model is the only
    pick, counts on the leaderboard). ``casual`` runs pick model+config+max-turns,
    have no gates, and never reach the leaderboard. Continues are always casual.
    """

    official = "official"
    casual = "casual"


class RunStatus(str, Enum):
    """Run lifecycle state.

    ``queued`` → ``running`` → terminal. Terminal states:
      - ``completed``  ran to its natural end (official: final ladder gate; casual: max-turns).
      - ``terminated`` referee killed it on a missed gate deadline (official only).
      - ``crashed``    the process died unexpectedly.
      - ``cancelled``  user removed/stopped it; a cancelled official run is voided.
    """

    queued = "queued"
    running = "running"
    completed = "completed"
    terminated = "terminated"
    crashed = "crashed"
    cancelled = "cancelled"


class RecordView(str, Enum):
    """Which presentation the recorder captures.

    ``simple`` = the 1:1 recording view (game screen + turn box) at a square
    viewport. ``detailed`` = the whole wide spectate instrument panel.
    """

    simple = "simple"
    detailed = "detailed"


class RecordSpeed(str, Enum):
    """How the recording treats the model's response time.

    ``realtime`` keeps every pause at its true length. ``cut_thinking`` records
    only the execution window of each turn — from ``llm_output`` (the turn starts
    executing) to just after ``screen_settled`` — so the think is not in the file.
    """

    realtime = "realtime"
    cut_thinking = "cut-thinking"


class RecordSpec(BaseModel):
    """Opt-in MP4 recording settings for one run.

    Absent (``None``) on a queued run means "don't record" — recording is never
    the default; it costs a headless browser and an encoder for the whole run.
    """

    view: RecordView = RecordView.simple
    speed: RecordSpeed = RecordSpeed.realtime
    fps: int = 30


# The config family the leaderboard ranks. Module-level (not a class attribute)
# because pydantic reads underscore-prefixed class attributes as private-attr
# declarations, and a ClassVar here would still read as a per-run field. Bumping
# it is a deliberate board reset — see ``RunSummary.leaderboard_eligible``.
LEADERBOARD_CONFIG_PREFIX = "config-5."


class RunSummary(BaseModel):
    """Flat, denormalized per-run index entry (Plan "run_summary.json schema").

    Every field is derivable from a run folder (config + events + referee
    scorecard), so the whole index rebuilds by scanning ``local/runs/*``.
    """

    run_id: str
    label: str | None = None
    kind: RunKind
    model: str
    model_resolved: str | None = None
    config_stem: str | None = None
    benchmark: str | None = None
    benchmark_version: str | None = None
    status: RunStatus
    started_at: str | None = None
    ended_at: str | None = None
    turns: int = 0
    duration_s: float = 0.0
    total_cost_usd: float = 0.0
    avg_cost_per_turn_usd: float = 0.0
    avg_s_per_turn: float = 0.0
    furthest_gate: str | None = None
    furthest_gate_turn: int | None = None
    gates_reached: int = 0
    total_gates: int = 0
    termination_reason: str | None = None
    continued_from: str | None = None
    resumed: bool = False
    # WHICH HARNESS ran this run — the ``agent_type`` the config selected:
    # "append_compact" (config-5.x, one append-only conversation + compaction) or
    # "current" (config-4.0 / 3.13, the sliding-window pydantic-ai agent).
    # ``run_summary.json["agent_type"]`` has been written since the append work
    # landed and read by nobody; this is its first reader. Defaults to "current"
    # because that is what its ABSENCE means on every legacy run on disk — the
    # writer only stamps the key when an AppendAgent was active (turn.py:2844).
    #
    # It is here rather than inferred from ``config_stem`` because the two answer
    # different questions: the stem says which FILE ran (and is what partitions
    # the leaderboard), this says how the agent was WIRED. A guard that must
    # refuse an append conversation (the model-swap continue) needs the second
    # one, and must keep working for a run whose config was renamed or hand-passed.
    #
    # Related but NOT the same field: ``trace.harness`` ({id,label}, built by
    # ``trace_build._harness``) splits the legacy side further into
    # ``task_master`` vs ``self_directed`` for the report's chip. Both read the
    # same fact — the config's ``agent_type`` — one hop apart: that one off
    # ``config.json`` directly, this one off ``run_summary.json["agent_type"]``,
    # which ``turn.py`` stamps from the live AppendAgent. They cannot disagree
    # about append vs legacy; they are deliberately different GRAINS, so this
    # stays the coarse canonical field (``current`` / ``append_compact``) that
    # index rows, guards and the API key on.
    harness: str = "current"
    # The turn cap the run actually ran under. Written to run_summary.json since
    # the cap moved onto the summary (it arrives as a call argument, not a config
    # key, so it is otherwise unrecoverable from the folder) and, until now,
    # absent from this row — while ``api.js``'s ``toRun`` already read
    # ``s.max_turns``. None on an official run: pace is its only bound.
    max_turns: int | None = None

    @property
    def leaderboard_eligible(self) -> bool:
        """True iff this run can post a leaderboard entry (locked decision #9).

        Three requirements, all necessary:

        - ``official`` — a casual run is never ranked, whatever it scored;
        - a terminal benchmark verdict (``completed`` = won, ``terminated`` =
          referee killed it on a missed gate deadline). A ``cancelled`` official
          run is voided;
        - the config stem is ``config-5.x``.

        The stem requirement is the 2026-09-07 harness flip. The board ranks
        "farthest, then fastest", and ``turns`` does not mean the same thing on
        both harnesses — legacy counts game turns PLUS TaskMaster invocations,
        append counts game turns only — so a mixed board would silently reward
        the append runs on the tiebreak. Partitioning on the config rather than
        rebasing the metric keeps every old number intact: the config-3.13 runs
        that used to hold the board stay in History with their badge and their
        scorecard, they are just no longer ranked against a different harness.
        A future config-5.1 joins the SAME board (same harness, same units); a
        6.0 would need this prefix moved, deliberately, with the board reset.

        Matched as a string prefix, not by parsing a version: the stem is
        whatever the run recorded, including ``None`` on a run dir whose name it
        could not be inferred from, and a missing stem must fail closed.
        """
        if self.kind != RunKind.official:
            return False
        if self.status not in (RunStatus.completed, RunStatus.terminated):
            return False
        return bool(self.config_stem) and self.config_stem.startswith(
            LEADERBOARD_CONFIG_PREFIX
        )


class QueuedRun(BaseModel):
    """One item in the serial queue — the spec "add new run" produces.

    ``config``/``max_turns``/``stop_at``/``max_spend_usd``/``gameplay`` are casual-only (official uses the
    frozen pokebench config + no max-turns, and ends at its own ladder). ``benchmark`` is official-only — which benchmark
    (e.g. ``pokebench-easy``) this run plays; it selects the gate ladder + the
    goal override. ``continue_from`` is set by Continue; it inherits the source
    run's kind — an official run continues official on the same ``benchmark``, a
    casual run continues casual.
    """

    queue_id: str
    kind: RunKind
    model: str
    config: str | None = None
    benchmark: str | None = None
    max_turns: int | None = None
    # Casual-only early finish line: the id of a story event (a gate on the full
    # ladder) that ends the run the moment the referee detects it. Runs
    # alongside ``max_turns`` — whichever lands first ends the run. None = turn
    # cap only. Official runs ignore it; a benchmark ends at its own ladder.
    stop_at: str | None = None
    # Casual-only spend ceiling in USD, all-in (Player + OCR + TaskMaster). The
    # third stop condition: whichever of max_turns / stop_at / max_spend_usd
    # lands first ends the run. None = no ceiling, which is what every existing
    # queue item means and what official runs always get (locked #8 — pace is
    # the only bound on a benchmark).
    max_spend_usd: float | None = None
    # Casual-only playstyle. Picks WHICH steering block the agent is given:
    # "exploration" -> freeplay_guidelines (wander, catch, roleplay),
    # "speed"       -> benchmark_guidelines (shortest path to the top goal).
    # Until now that choice was welded to `kind` — official always raced, casual
    # always explored — so there was no way to time a model on a casual run, or
    # to watch an official-style config just play. None = exploration, which is
    # what every casual run has always done, so existing queue items are
    # unchanged. Official ignores it: a benchmark always races.
    gameplay: str | None = None
    # Which game this run needs — a ROM id from ``configs/roms.yaml``. Casual-only
    # and None by default (= the registry's default ROM), so every pre-existing
    # queue item keeps meaning exactly what it meant. Official runs take their ROM
    # from the benchmark's ladder instead: a score has to come from the dump the
    # ladder was authored against. A continue inherits the source run's ROM.
    rom: str | None = None
    # Casual-only opening: a `label` from ``configs/starts.yaml``, scoped to this
    # run's ROM (e.g. "girl" on firered). None = that ROM's default start, which
    # is what every pre-existing queue item means. Official runs ignore it — a
    # benchmark starts from executor.CANONICAL_SAVE, because a choosable opening
    # would make two scores incomparable. A continue ignores it too: it resumes
    # the source run's own savepoint, so the opening was decided one run ago.
    start: str | None = None
    # Which NAMED provider-profile variant this run's append agent runs under —
    # a key from ``configs/provider-profiles.yaml``'s ``variants:`` block
    # (``gemma-guidance``, ``gemma-replay``, …), or None for the model's BASE
    # profile, which is what every run has had until now.
    #
    # Casual-only and append-only, both enforced at the API door
    # (``server._validate_provider_profile``): a variant changes the transport
    # contract — the endpoint tag it was probed on, whether prior reasoning is
    # replayed — so an official run is restricted to the base profile
    # (decision Q3) or two scores would not be comparable, and a legacy config
    # resolves no profile at all (``resolve_provider_profile`` returns before
    # reading the catalog, and raises outright on a named one).
    #
    # WHO READS IT after enqueue: ``executor.build_run_config`` passes it to
    # ``cli.runner.prepare_config(provider_profile=…)`` → ``config.load_config``,
    # which writes it to ``config["provider_profiles"]["name"]`` →
    # ``provider_profiles.resolve_provider_profile`` merges that variant's
    # ``settings`` over the base profile and stamps ``_provider_profile["name"]``
    # → back in ``prepare_config``, that name suffixes ``run_name`` and
    # ``run_label``, so the run dir and every card say which arm ran (finding
    # #31); ``AppendAgent._body`` then builds the request from the merged
    # profile. Until this field existed the whole chain was reachable only from
    # ``pokemon run --provider-profile``, which fights the control center for
    # mGBA — so the documented Gemma A/B was control-center-unreachable.
    provider_profile: str | None = None
    continue_from: str | None = None
    # Optional TaskMaster model override (casual only). None → inherit the
    # source/config/freeplay-default resolution. The Player model rides on
    # ``model``; on a casual continue the UI may set both to new picks.
    task_master_model: str | None = None
    # Opt-in MP4 recording. None = not recorded (the default). Applies to both
    # kinds — an official run is exactly the one you'd most want a video of.
    record: RecordSpec | None = None
    enqueued_at: str
