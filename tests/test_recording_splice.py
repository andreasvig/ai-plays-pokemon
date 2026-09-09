"""Continuing a run continues its video (Andreas, 2026-09-09).

"When you continue a run which has stopped, either through the CLI or the
interface, I would like it to also automatically continue the video, or at
least start a new recording and splice with the old." A finished mp4 cannot be
appended to, so the mechanism is the second half: the continue records its own
segment in the SOURCE run's view, then ``recorder.splice_continued`` joins
source + segment into the continued run's ``recording.mp4`` (stream copy) and
keeps the segment as ``recording-segment.mp4``.

The splice tests drive the real ffmpeg/ffprobe on tiny synthetic clips and are
skipped where those binaries are missing. The defaulting tests cover the three
entry points: the executor (queue), the continue endpoint (UI + ``pokemon runs
continue``) and ``pokemon run --continue``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.dashboard import recorder

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _clip(path: Path, *, seconds: float = 0.5, size: str = "320x240", color: str = "red") -> Path:
    """A synthetic H.264 clip with the recorder's own output settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s={size}:r=30:d={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(path)],
        check=True, capture_output=True,
    )
    return path


def _continued(tmp_path: Path, *, turn: int = 12) -> tuple[Path, Path, dict]:
    src = tmp_path / "runs" / "2026-09-08_20-22-08_config-5.0__glm"
    run = tmp_path / "runs" / "2026-09-09_16-00-00_config-5.0__glm_continued_from_turn_12"
    run.mkdir(parents=True)
    src.mkdir(parents=True)
    (run / "run_summary.json").write_text(json.dumps({"run_id": run.name, "status": "completed"}))
    config = {"_continued_from": str(src), "_continued_from_turn": turn}
    return src, run, config


@needs_ffmpeg
def test_splice_joins_the_source_video_and_this_segment(tmp_path: Path, capsys):
    src, run, config = _continued(tmp_path)
    _clip(src / "recording.mp4", seconds=1.0, color="red")
    _clip(run / "recording.mp4", seconds=0.5, color="blue")
    src_size = (src / "recording.mp4").stat().st_size

    spliced = recorder.splice_continued(config, run)

    assert [s["file"] for s in spliced] == ["recording.mp4"]
    joined = recorder.probe_video(run / "recording.mp4")
    assert joined["duration_s"] == pytest.approx(1.5, abs=0.1)
    # the segment on its own survives beside the chain; the source is untouched
    seg = recorder.probe_video(run / "recording-segment.mp4")
    assert seg["duration_s"] == pytest.approx(0.5, abs=0.1)
    assert (src / "recording.mp4").stat().st_size == src_size
    # the lineage is readable off the summary, by run id (never a path)
    summary = json.loads((run / "run_summary.json").read_text())
    (rec,) = summary["recording_splice"]
    assert rec["source_run"] == src.name and rec["resumed_at_turn"] == 12
    assert rec["source_s"] == pytest.approx(1.0, abs=0.1) and rec["total_s"] == pytest.approx(1.5, abs=0.1)
    assert "/" not in rec["source_run"]
    assert "spliced recording.mp4" in capsys.readouterr().out


@needs_ffmpeg
def test_both_view_splices_each_file_onto_its_own_source(tmp_path: Path):
    src, run, config = _continued(tmp_path)
    for name in ("recording.mp4", "recording-simple.mp4"):
        _clip(src / name, seconds=0.5)
        _clip(run / name, seconds=0.5, color="green")
    spliced = recorder.splice_continued(config, run)
    assert sorted(s["file"] for s in spliced) == ["recording-simple.mp4", "recording.mp4"]
    assert (run / "recording-segment.mp4").is_file() and (run / "recording-simple-segment.mp4").is_file()
    for name in ("recording.mp4", "recording-simple.mp4"):
        assert recorder.probe_video(run / name)["duration_s"] == pytest.approx(1.0, abs=0.1)


@needs_ffmpeg
def test_mismatched_streams_are_reported_and_left_alone(tmp_path: Path, capsys):
    """A continue recorded in another view (other frame size) cannot be
    stream-copied onto the source; the segment stands on its own, as before."""
    src, run, config = _continued(tmp_path)
    _clip(src / "recording.mp4", size="320x240")
    _clip(run / "recording.mp4", size="160x120")
    before = (run / "recording.mp4").read_bytes()

    assert recorder.splice_continued(config, run) == []
    assert (run / "recording.mp4").read_bytes() == before
    assert not (run / "recording-segment.mp4").exists()
    out = capsys.readouterr().out
    assert "not spliced" in out and "width 320 vs 160" in out
    assert "recording_splice" not in json.loads((run / "run_summary.json").read_text())


@needs_ffmpeg
def test_a_source_without_video_keeps_the_segment_and_says_where_it_starts(tmp_path: Path, capsys):
    src, run, config = _continued(tmp_path, turn=100)
    _clip(run / "recording.mp4")
    assert recorder.splice_continued(config, run) == []
    assert (run / "recording.mp4").is_file()
    assert "starts at turn 100" in capsys.readouterr().out


def test_a_segment_without_video_does_not_borrow_the_source_file(tmp_path: Path, capsys):
    src, run, config = _continued(tmp_path)
    (src / "recording.mp4").write_bytes(b"x" * 10)
    assert recorder.splice_continued(config, run) == []
    assert not (run / "recording.mp4").exists()
    assert "nothing was recorded for this segment" in capsys.readouterr().out


def test_a_fresh_run_is_a_noop(tmp_path: Path):
    run = tmp_path / "fresh"
    run.mkdir()
    (run / "recording.mp4").write_bytes(b"x")
    assert recorder.splice_continued({}, run) == []
    assert (run / "recording.mp4").read_bytes() == b"x"


def test_source_record_spec_reads_the_source_config(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    assert recorder.source_record_spec(src) is None  # no config at all
    (src / "config.json").write_text(json.dumps({"llm_model": "x"}))
    assert recorder.source_record_spec(src) is None  # recorded nothing
    (src / "config.json").write_text(json.dumps({"_record": {"view": "detailed", "speed": "cut-thinking"}}))
    spec = recorder.source_record_spec(src)
    assert spec["view"] == "detailed" and spec["speed"] == "cut-thinking" and spec["fps"] == 30
    (src / "config.json").write_text(json.dumps({"_record": {"view": "hologram"}}))
    assert recorder.source_record_spec(src) is None  # unreadable → treated as unrecorded


# ── the executor: a continue's record spec vs the source's ──────────────────


def _drain_continue(tmp_path: Path, *, item_record, source_record):
    """Drain one casual continue through RunExecutor with a fake run_fn that
    captures the config; returns config['_record'] (or the MISSING sentinel)."""
    from src.app.executor import RunExecutor
    from src.app.models import RunKind
    from src.app.queue_manager import QueueManager
    from src.app.run_index import RunIndex

    runs_root = tmp_path / "runs"
    src = runs_root / "2026-09-08_src_config-5.0__glm"
    (src / "savepoints" / "turn_30").mkdir(parents=True)
    (src / "run_summary.json").write_text(json.dumps({"session": {"llm_alias": "glm"}}))
    seen: dict = {}

    def fake_run_fn(handle, config, *, turns, snapshot, open_browser=False, on_run_dir=None, should_stop=None):
        seen["config"] = config
        rd = runs_root / "cont"
        rd.mkdir(exist_ok=True)
        (rd / "run_summary.json").write_text("{}")
        if on_run_dir:
            on_run_dir(rd)
        return rd

    def fake_continue(path):
        cfg = {"task": {}, "task_master": {}}
        if source_record is not None:
            cfg["_record"] = source_record  # what config.json carries into the continue
        return cfg, src / "savepoints" / "turn_30"

    class _S:
        handle = {}
        _busy = False

        def status(self):
            class St:
                busy = _S._busy
            return St()

        def set_busy(self, v):
            _S._busy = v

    index = RunIndex(tmp_path / "idx.json", runs_root)
    index.load()
    ex = RunExecutor(
        supervisor=_S(), queue_manager=QueueManager(tmp_path / "q.json"), run_index=index,
        runs_root=runs_root, saves_dir=tmp_path / "saves", run_fn=fake_run_fn,
        prepare_config_fn=lambda *a, **k: {"task": {}}, continue_fn=fake_continue,
    )
    ex.queue.enqueue(RunKind.casual, "glm", config=None, max_turns=3, continue_from=src.name, record=item_record)
    ex.drain_once()
    return seen["config"].get("_record", "MISSING")


SOURCE = {"view": "detailed", "speed": "cut-thinking", "fps": 30,
          "show_model": True, "show_elapsed": True, "show_cost": True}


def test_executor_takes_the_source_view_when_the_continue_chose_none(tmp_path: Path):
    rec = _drain_continue(tmp_path, item_record={"speed": "cut-thinking"}, source_record=SOURCE)
    assert rec["view"] == "detailed"  # not the casual default (simple)


def test_executor_falls_back_to_the_kind_default_for_an_unrecorded_source(tmp_path: Path):
    rec = _drain_continue(tmp_path, item_record={}, source_record=None)
    assert rec["view"] == "simple"


def test_executor_honours_an_explicit_view_over_the_source(tmp_path: Path):
    rec = _drain_continue(tmp_path, item_record={"view": "simple"}, source_record=SOURCE)
    assert rec["view"] == "simple"


def test_executor_strips_the_inherited_spec_when_the_item_has_none(tmp_path: Path):
    """`record: null` on the queue item means no video — also on a continue,
    whose config arrives carrying the source's `_record`. Inheritance is the
    endpoint's job, not a side effect of loading the source config."""
    assert _drain_continue(tmp_path, item_record=None, source_record=SOURCE) == "MISSING"


# ── pokemon run --continue ───────────────────────────────────────────────────


def _args(**over):
    base = dict(record=None, no_record=False, record_speed="cut-thinking", record_fps=30, record_show=None)
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture
def recorder_available(monkeypatch):
    monkeypatch.setattr(recorder, "recorder_preflight", lambda: None)


def test_cli_continue_keeps_the_source_spec(recorder_available, capsys):
    from src.cli.runner import apply_cli_record

    prepared = [{"_record": dict(SOURCE)}]
    apply_cli_record(_args(), prepared, continuing=True)
    assert prepared[0]["_record"]["view"] == "detailed"
    assert "like the source run" in capsys.readouterr().out


def test_cli_continue_explicit_record_overrides_the_source(recorder_available):
    from src.cli.runner import apply_cli_record

    prepared = [{"_record": dict(SOURCE)}]
    apply_cli_record(_args(record="simple", record_speed="realtime"), prepared, continuing=True)
    assert prepared[0]["_record"]["view"] == "simple" and prepared[0]["_record"]["speed"] == "realtime"


def test_cli_no_record_drops_the_inherited_spec_too(recorder_available):
    """Before 2026-09-09 --no-record on a continue still recorded: the source's
    `_record` rode in on config.json and nothing removed it."""
    from src.cli.runner import apply_cli_record

    prepared = [{"_record": dict(SOURCE)}]
    apply_cli_record(_args(no_record=True), prepared, continuing=True)
    assert "_record" not in prepared[0]


def test_cli_fresh_run_still_defaults_to_simple(recorder_available):
    from src.cli.runner import apply_cli_record

    prepared = [{}, {}]
    apply_cli_record(_args(), prepared, continuing=False)
    assert all(c["_record"]["view"] == "simple" and c["_record"]["speed"] == "cut-thinking" for c in prepared)


def test_cli_default_degrades_when_the_recorder_is_unavailable(monkeypatch, capsys):
    from src.cli.runner import apply_cli_record

    monkeypatch.setattr(recorder, "recorder_preflight", lambda: "ffmpeg not on PATH")
    prepared = [{"_record": dict(SOURCE)}]
    apply_cli_record(_args(), prepared, continuing=True)
    assert "_record" not in prepared[0]
    assert "not recording" in capsys.readouterr().out


# ── the projection carries the spec so the dialog can seed from it ──────────


def test_projection_carries_the_record_spec(tmp_path: Path):
    from src.app.projection import PROJECTION_VERSION, project_run_dir

    run = tmp_path / "2026-09-09_10-00-00_config-5.0__glm"
    run.mkdir()
    (run / "run_summary.json").write_text(json.dumps({
        "run_id": run.name, "status": "completed",
        "session": {"llm_alias": "glm(low)", "total_turns": 3, "duration_seconds": 1.0},
        "cost": {"total_usd": 0.0},
    }))
    (run / "config.json").write_text(json.dumps({"_record": {"view": "both", "speed": "realtime"}}))
    s = project_run_dir(run)
    assert s.record["view"] == "both" and s.record["speed"] == "realtime"
    assert s.projection_version == PROJECTION_VERSION >= 2
    (run / "config.json").write_text(json.dumps({}))
    assert project_run_dir(run).record is None
