"""Tests for the run recorder (src/dashboard/recorder.py) and its wiring.

Split three ways:

  1. Spec normalisation — the edge between a CLI flag / an API body and the
     recorder. Everything downstream trusts its output, so a bad view or speed
     has to die here.
  2. The cut-thinking gate — a pure state machine, tested with an injected
     clock. This is the part that decides what is IN the video, so it is tested
     without a browser, an encoder, or a run.
  3. Wiring — that a queued run's record spec actually reaches the one place
     that starts a recorder, and that a run WITHOUT one is untouched.

Nothing here launches Chrome or ffmpeg; those are exercised by the live smoke
run documented in docs/recording.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.dashboard.recorder import (
    SETTLE_TAIL_S,
    VIEWPORTS,
    RecordGate,
    normalize_spec,
    record_url,
)


# ───────────────────────── 1. spec normalisation ─────────────────────────


def test_normalize_none_means_no_recording():
    assert normalize_spec(None) is None
    assert normalize_spec(False) is None


def test_normalize_bare_view_string():
    assert normalize_spec("detailed") == {
        "view": "detailed", "speed": "realtime", "fps": 30
    }


def test_normalize_defaults_to_simple_realtime_30():
    assert normalize_spec({}) == {"view": "simple", "speed": "realtime", "fps": 30}


def test_normalize_accepts_the_pydantic_model():
    from src.app.models import RecordSpec, RecordSpeed, RecordView

    spec = RecordSpec(view=RecordView.detailed, speed=RecordSpeed.cut_thinking, fps=24)
    assert normalize_spec(spec) == {
        "view": "detailed", "speed": "cut-thinking", "fps": 24
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"view": "wide"},              # not a known presentation
        {"speed": "fast"},             # not a known speed
        {"fps": 0},                    # below the floor
        {"fps": 120},                  # above the ceiling
    ],
)
def test_normalize_rejects_garbage(bad):
    with pytest.raises(ValueError):
        normalize_spec(bad)


def test_every_view_has_a_viewport():
    """A view the spec accepts but the recorder can't size would crash at start."""
    for view in ("simple", "detailed"):
        assert view in VIEWPORTS
        w, h = VIEWPORTS[view]
        # x264 requires even dimensions; an odd one fails at encode time, long
        # after the run has been spent.
        assert w % 2 == 0 and h % 2 == 0


def test_simple_viewport_is_square():
    """The simple view's stage is min(100vw,100vh) — a square viewport makes it
    fill the frame exactly, which is what "just the 1:1 view" means."""
    w, h = VIEWPORTS["simple"]
    assert w == h


def test_record_url_pins_the_run():
    url = record_url(3420, "2026-08-01_run-x", "simple")
    assert "record=1" in url
    assert "view=simple" in url
    # The pinned run id is what keeps the recorder in the view after the run
    # ends (active_run_id goes null there).
    assert "run=2026-08-01_run-x" in url


# ───────────────────────── 2. the cut-thinking gate ─────────────────────────


def test_realtime_gate_is_always_open():
    g = RecordGate("realtime")
    assert g.is_open(0.0)
    g.on_event("turn_start", 1.0)
    assert g.is_open(1.0), "realtime must keep recording through the think"
    g.on_event("screen_settled", 2.0)
    assert g.is_open(99.0)


def test_cut_thinking_starts_shut():
    """Nothing is recorded before the first turn actually executes."""
    g = RecordGate("cut-thinking")
    assert not g.is_open(0.0)
    g.on_event("turn_start", 1.0)
    assert not g.is_open(1.5), "turn_start is the model beginning to THINK"


def test_cut_thinking_records_the_execution_window():
    g = RecordGate("cut-thinking")
    g.on_event("turn_start", 0.0)
    assert not g.is_open(3.0)          # 3s of model latency: not in the video
    g.on_event("llm_output", 4.0)      # answered — the turn starts executing
    assert g.is_open(4.0)
    assert g.is_open(5.5)              # buttons pressing, screen moving
    g.on_event("screen_settled", 6.0)
    assert g.is_open(6.0 + SETTLE_TAIL_S / 2), "the settled screen is the payoff frame"
    assert not g.is_open(6.0 + SETTLE_TAIL_S + 0.01)


def test_cut_thinking_reopens_every_turn():
    g = RecordGate("cut-thinking")
    for turn in range(3):
        base = turn * 20.0
        g.on_event("turn_start", base)
        assert not g.is_open(base + 1.0)
        g.on_event("llm_output", base + 5.0)
        assert g.is_open(base + 5.0)
        g.on_event("screen_settled", base + 7.0)
        assert not g.is_open(base + 7.0 + SETTLE_TAIL_S + 0.01)


def test_a_turn_that_never_settles_does_not_leak_into_the_next_think():
    """If screen_settled is lost (an error mid-turn), the next turn_start shuts
    the gate — otherwise the whole of the next model call lands in the video,
    which is precisely what cut-thinking exists to remove."""
    g = RecordGate("cut-thinking")
    g.on_event("llm_output", 1.0)
    assert g.is_open(1.0)
    g.on_event("turn_start", 9.0)      # next turn began; no settle ever came
    assert not g.is_open(9.01)


def test_gate_self_closes_after_max_open():
    """Belt-and-braces for a run that stops emitting events entirely."""
    g = RecordGate("cut-thinking", max_open_s=10.0)
    g.on_event("llm_output", 0.0)
    assert g.is_open(9.0)
    assert not g.is_open(10.5)


def test_button_sequence_does_not_open_the_gate():
    """`button_sequence` is logged AFTER press_button_list() returns — it marks
    the END of pressing. Opening on it would start each clip after the action it
    is supposed to show. (Same trap SimpleView's phase machine documents.)"""
    g = RecordGate("cut-thinking")
    g.on_event("turn_start", 0.0)
    g.on_event("button_sequence", 1.0)
    assert not g.is_open(1.0)


# ───────────────────────── 3. wiring ─────────────────────────


def test_queued_run_round_trips_a_record_spec(tmp_path: Path):
    """The spec has to survive queue.json — the queue is persisted between the
    enqueue and the run."""
    from src.app.models import RunKind
    from src.app.queue_manager import QueueManager

    qpath = tmp_path / "queue.json"
    q = QueueManager(qpath)
    q.enqueue(
        RunKind.casual, "claude-haiku-4-5(medium)",
        config="config-3.13",
        record={"view": "detailed", "speed": "cut-thinking", "fps": 24},
    )
    raw = json.loads(qpath.read_text())
    assert raw["items"][0]["record"] == {
        "view": "detailed", "speed": "cut-thinking", "fps": 24
    }

    reloaded = QueueManager(qpath)
    assert reloaded.items[0].record.view.value == "detailed"
    assert reloaded.items[0].record.speed.value == "cut-thinking"


def test_queued_run_without_record_stays_none(tmp_path: Path):
    from src.app.models import RunKind
    from src.app.queue_manager import QueueManager

    q = QueueManager(tmp_path / "queue.json")
    item = q.enqueue(RunKind.official, "claude-haiku-4-5(medium)", benchmark=None)
    assert item.record is None


def test_executor_stamps_the_spec_onto_the_run_config(tmp_path: Path):
    """The record spec reaches run_single_loop via `config['_record']`.

    That indirection is the point of this test: it keeps the run_fn signature
    fixed (so every injected test fake keeps working) while still giving the
    recorder its settings. If someone "cleans it up" into a kwarg, this fails.
    """
    from src.app.models import RecordSpec, RecordSpeed, RecordView, RunKind
    from src.app.queue_manager import QueueManager
    from src.app.executor import RunExecutor

    seen: dict = {}

    def fake_run_fn(handle, config, *, turns, snapshot, open_browser=False,
                    on_run_dir=None, should_stop=None):
        seen["config"] = config
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_summary.json").write_text("{}")
        if on_run_dir:
            on_run_dir(run_dir)
        return run_dir

    class FakeStatus:
        busy = False

    class FakeSupervisor:
        handle = {}

        def status(self):
            return FakeStatus()

        def set_busy(self, _v):
            FakeStatus.busy = _v

    class FakeIndex:
        def upsert(self, *a, **k):
            pass

    q = QueueManager(tmp_path / "queue.json")
    q.enqueue(
        RunKind.casual, "claude-haiku-4-5(medium)", config="config-3.13",
        max_turns=2,
        record=RecordSpec(view=RecordView.simple, speed=RecordSpeed.cut_thinking),
    )

    ex = RunExecutor(
        supervisor=FakeSupervisor(),
        queue_manager=q,
        run_index=FakeIndex(),
        runs_root=tmp_path / "runs",
        saves_dir=tmp_path / "saves",
        run_fn=fake_run_fn,
        prepare_config_fn=lambda path, model, **kw: {"llm_model": model, "task": {}},
    )
    ex.drain_once()

    assert seen["config"]["_record"] == {
        "view": "simple", "speed": "cut-thinking", "fps": 30
    }


def test_executor_leaves_unrecorded_runs_alone(tmp_path: Path):
    """No `_record` key at all on a normal run — `maybe_start` must see nothing
    rather than a falsy spec it has to interpret."""
    from src.app.models import RunKind
    from src.app.queue_manager import QueueManager
    from src.app.executor import RunExecutor

    seen: dict = {}

    def fake_run_fn(handle, config, *, turns, snapshot, open_browser=False,
                    on_run_dir=None, should_stop=None):
        seen["config"] = config
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run_summary.json").write_text("{}")
        return run_dir

    class FakeStatus:
        busy = False

    class FakeSupervisor:
        handle = {}

        def status(self):
            return FakeStatus()

        def set_busy(self, _v):
            FakeStatus.busy = _v

    class FakeIndex:
        def upsert(self, *a, **k):
            pass

    q = QueueManager(tmp_path / "queue.json")
    q.enqueue(RunKind.casual, "claude-haiku-4-5(medium)", config="config-3.13", max_turns=2)

    ex = RunExecutor(
        supervisor=FakeSupervisor(),
        queue_manager=q,
        run_index=FakeIndex(),
        runs_root=tmp_path / "runs",
        saves_dir=tmp_path / "saves",
        run_fn=fake_run_fn,
        prepare_config_fn=lambda path, model, **kw: {"llm_model": model, "task": {}},
    )
    ex.drain_once()

    assert "_record" not in seen["config"]


def test_ffmpeg_log_is_deleted_not_leaked(tmp_path: Path):
    """ffmpeg's stderr scratch file must not survive the recording.

    It is NamedTemporaryFile(delete=False) because ffmpeg needs a real path to
    write to, so nothing removes it for us — 9 empty logs accumulated in the
    system temp dir over one afternoon of recording before this was fixed.
    """
    import tempfile

    from src.dashboard.recorder import RunRecorder

    rec = RunRecorder(run_id="r", run_dir=tmp_path, port=1,
                      spec={"view": "simple", "speed": "realtime", "fps": 30})
    rec._ff_err = tempfile.NamedTemporaryFile(
        prefix="pokebench-rec-", suffix=".log", delete=False
    )
    log = Path(rec._ff_err.name)
    log.write_bytes(b"some encoder noise\n")
    assert log.exists()

    rec._drop_ff_log()
    assert not log.exists()
    rec._drop_ff_log()          # idempotent — stop() and start() both call it


def test_ffmpeg_error_is_read_before_the_log_is_dropped(tmp_path: Path):
    """The tail has to be folded into `error` while the file still exists.

    Ordering trap: `_teardown()` runs before the error is read, so the delete
    cannot live there — an empty mp4 would then report no reason at all.
    """
    import tempfile

    from src.dashboard.recorder import RunRecorder

    rec = RunRecorder(run_id="r", run_dir=tmp_path, port=1,
                      spec={"view": "simple", "speed": "realtime", "fps": 30})
    rec._ff_err = tempfile.NamedTemporaryFile(
        prefix="pokebench-rec-", suffix=".log", delete=False
    )
    Path(rec._ff_err.name).write_text("frame= 0\nTask finished with error code: -22\n")
    assert "-22" in (rec._ffmpeg_error() or "")
    rec._drop_ff_log()


def test_maybe_start_is_a_noop_without_a_spec(tmp_path: Path):
    """The hook run_single_loop calls unconditionally must cost nothing on the
    overwhelmingly common path — no browser, no import of the server port."""
    from src.dashboard import recorder

    assert recorder.maybe_start({}, tmp_path, "run-x") is None
    assert recorder.finish(None) is None


# ─────────────────── 4. the History "watch" surface ───────────────────


@pytest.fixture
def api(tmp_path: Path):
    """TestClient over a control plane whose runs_root is a real temp dir.

    `has_recording` is derived from disk, so the runs_root has to be real —
    a stub path would make every row False and the test vacuous.
    """
    import types

    from fastapi.testclient import TestClient

    from src.app.models import RunKind, RunStatus, RunSummary
    from src.dashboard import server

    runs = tmp_path / "runs"
    (runs / "with_vid").mkdir(parents=True)
    (runs / "no_vid").mkdir(parents=True)
    (runs / "empty_vid").mkdir(parents=True)
    (runs / "with_vid" / "recording.mp4").write_bytes(b"\x00" * 2048)
    (runs / "empty_vid" / "recording.mp4").write_bytes(b"")   # 0 bytes = failed encode

    def mk(rid):
        return RunSummary(run_id=rid, kind=RunKind.casual, model="m",
                          status=RunStatus.completed, turns=5)

    entries = [mk("with_vid"), mk("no_vid"), mk("empty_vid")]

    class Index:
        def all(self): return list(entries)
        def get(self, rid): return next((e for e in entries if e.run_id == rid), None)

    server.configure_control_plane(
        queue_manager=object(),
        executor=types.SimpleNamespace(runs_root=runs),
        run_index=Index(),
    )
    yield TestClient(server.app)
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


def test_history_flags_only_runs_that_have_a_video(api):
    rows = {r["run_id"]: r["has_recording"] for r in api.get("/api/runs").json()}
    assert rows == {"with_vid": True, "no_vid": False, "empty_vid": False}


def test_zero_byte_recording_does_not_count(api):
    """A failed encode leaves a 0-byte mp4. Flagging it would put a ▶ button on
    a run whose video is unplayable."""
    assert api.get("/api/runs/empty_vid").json()["has_recording"] is False
    assert api.get("/api/runs/empty_vid/recording.mp4").status_code == 404


def test_recording_route_serves_inline_and_accepts_ranges(api):
    """Range is what makes the player's scrub bar seek; `inline` is what stops
    the browser treating the <video> source as a download."""
    r = api.get("/api/runs/with_vid/recording.mp4")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-disposition"].startswith("inline")

    part = api.get("/api/runs/with_vid/recording.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100


def test_recording_route_404s_when_absent(api):
    assert api.get("/api/runs/no_vid/recording.mp4").status_code == 404
    assert api.get("/api/runs/nope/recording.mp4").status_code == 404


def test_flag_follows_the_file(api, tmp_path: Path):
    """Derived per request, not stored — deleting the mp4 to reclaim space must
    drop the button rather than leave a ▶ that 404s."""
    assert api.get("/api/runs/with_vid").json()["has_recording"] is True
    (tmp_path / "runs" / "with_vid" / "recording.mp4").unlink()
    assert api.get("/api/runs/with_vid").json()["has_recording"] is False


def test_maybe_start_declines_a_bad_spec_instead_of_raising(tmp_path: Path, capsys):
    """A malformed spec must never take a real run down with it."""
    from src.dashboard import recorder

    assert recorder.maybe_start({"_record": {"view": "nope"}}, tmp_path, "run-x") is None
    assert "recording disabled" in capsys.readouterr().out
